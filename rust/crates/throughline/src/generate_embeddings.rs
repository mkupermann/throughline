//! Generate embeddings for conversations / messages / memory chunks.

use crate::embedding::{self, EmbeddingBackend};
use anyhow::Result;
use postgres::GenericClient;

#[derive(Debug, Default, Clone)]
pub struct EmbedSummary {
    pub embedded: usize,
    pub skipped: usize,
    pub errors: usize,
}

/// `"[a,b,…]"` text literal for a pgvector value (`$n::text::vector`).
pub fn vector_literal(v: &[f32]) -> String {
    format!(
        "[{}]",
        v.iter()
            .map(|x| x.to_string())
            .collect::<Vec<_>>()
            .join(",")
    )
}

/// How far a model's vectors cover the notes and messages that
/// [`embed_pending`] would embed for it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedProgress {
    pub model: String,
    pub notes_done: i64,
    pub notes_total: i64,
    pub messages_done: i64,
    pub messages_total: i64,
}

impl EmbedProgress {
    pub fn notes_missing(&self) -> i64 {
        (self.notes_total - self.notes_done).max(0)
    }

    pub fn messages_missing(&self) -> i64 {
        (self.messages_total - self.messages_done).max(0)
    }

    pub fn is_complete(&self) -> bool {
        self.notes_missing() == 0 && self.messages_missing() == 0
    }
}

/// Notes (not forgotten) and messages with text, and how many of them have a vector
/// for `backend.model` in its column. Notes count only when `actor` sees them;
/// the embedding job itself embeds every note.
pub fn embed_progress<C: GenericClient>(
    client: &mut C,
    actor: &crate::access::Actor,
    backend: &EmbeddingBackend,
) -> Result<EmbedProgress> {
    let column = backend.column;
    let count = |client: &mut C, sql: String, model: Option<&str>| -> Result<i64> {
        let row = match model {
            Some(m) => client.query_one(&sql, &[&m])?,
            None => client.query_one(&sql, &[])?,
        };
        Ok(row.get(0))
    };
    let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> = Vec::new();
    let seen = crate::access::push_visibility(actor, "c", &mut params);
    let notes_total: i64 = client
        .query_one(
            &format!(
                "SELECT count(*) FROM memory_chunks c \
                 WHERE COALESCE(c.status, 'active') <> 'forgotten' \
                   AND COALESCE(c.content, '') <> '' AND {seen}"
            ),
            &params,
        )?
        .get(0);
    let messages_total = count(
        client,
        "SELECT count(*) FROM messages WHERE COALESCE(content, '') <> ''".into(),
        None,
    )?;
    let mut params: Vec<&(dyn postgres::types::ToSql + Sync)> = vec![&backend.model];
    let seen = crate::access::push_visibility(actor, "c", &mut params);
    let notes_done: i64 = client
        .query_one(
            &format!(
                "SELECT count(*) FROM embeddings e \
                 JOIN memory_chunks c ON c.id = e.source_id \
                 WHERE e.source_type = 'memory_chunk' AND e.model = $1 \
                   AND e.{column} IS NOT NULL \
                   AND COALESCE(c.status, 'active') <> 'forgotten' \
                   AND COALESCE(c.content, '') <> '' AND {seen}"
            ),
            &params,
        )?
        .get(0);
    let messages_done = count(
        client,
        format!(
            "SELECT count(*) FROM embeddings e \
             JOIN messages m ON m.id = e.source_id \
             WHERE e.source_type = 'message' AND e.model = $1 \
               AND e.{column} IS NOT NULL AND COALESCE(m.content, '') <> ''"
        ),
        Some(&backend.model),
    )?;
    Ok(EmbedProgress {
        model: backend.model.clone(),
        notes_done,
        notes_total,
        messages_done,
        messages_total,
    })
}

/// Result of one `embed` job: a run per model, the active one first, and the
/// target's error when it could not be used.
#[derive(Debug, Default)]
pub struct EmbedJob {
    pub runs: Vec<(String, EmbedSummary)>,
    pub target_error: Option<String>,
}

/// Embed for the active model, then for the target with the same `limit`.
/// A target that failed to resolve never stops the active run.
pub fn embed_job_with<C: GenericClient, F: Fn(&EmbeddingBackend, &str) -> Result<Vec<f32>>>(
    client: &mut C,
    active: &EmbeddingBackend,
    target: Result<Option<EmbeddingBackend>>,
    limit: usize,
    embed: F,
) -> Result<EmbedJob> {
    let mut job = EmbedJob::default();
    let summary = embed_pending_with(client, active, limit, |t| embed(active, t))?;
    job.runs.push((embedding::label(active), summary));
    match target {
        Ok(Some(t)) => {
            let summary = embed_pending_with(client, &t, limit, |text| embed(&t, text))?;
            job.runs.push((embedding::label(&t), summary));
        }
        Ok(None) => {}
        Err(e) => job.target_error = Some(format!("{e:#}")),
    }
    Ok(job)
}

/// Embed unembedded memory notes first, then messages with the rest of `limit`.
pub fn embed_pending<C: GenericClient>(
    client: &mut C,
    backend: &EmbeddingBackend,
    limit: usize,
) -> Result<EmbedSummary> {
    embed_pending_with(client, backend, limit, |text| {
        embedding::embed_text(backend, text, embedding::TextKind::Document)
    })
}

/// Like [`embed_pending`] with a caller-supplied embedder (tests, no Ollama).
/// Forgotten notes are skipped; superseded and status-less ones count as candidates.
pub fn embed_pending_with<C: GenericClient, F: Fn(&str) -> Result<Vec<f32>>>(
    client: &mut C,
    backend: &EmbeddingBackend,
    limit: usize,
    embed: F,
) -> Result<EmbedSummary> {
    let mut summary = EmbedSummary::default();
    let column = backend.column;
    let notes = client.query(
        &format!(
            r#"
            SELECT c.id, left(c.content, 4000)
            FROM memory_chunks c
            WHERE COALESCE(c.status, 'active') <> 'forgotten'
              AND COALESCE(c.content, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.source_type = 'memory_chunk'
                  AND e.source_id = c.id
                  AND e.model = $1
                  AND e.{column} IS NOT NULL
              )
            ORDER BY c.id DESC
            LIMIT $2::bigint
            "#
        ),
        &[&backend.model, &(limit as i64)],
    );
    let notes = match notes {
        Ok(r) => r,
        Err(e) => {
            eprintln!("embed: note query skipped: {e}");
            Vec::new()
        }
    };
    embed_rows(client, backend, "memory_chunk", notes, &embed, &mut summary);

    let remaining = limit.saturating_sub(summary.embedded + summary.errors);
    if remaining == 0 {
        return Ok(summary);
    }

    // Messages without a vector in this backend's column.
    let rows = client.query(
        &format!(
            r#"
            SELECT m.id, left(COALESCE(m.content, ''), 4000)
            FROM messages m
            WHERE COALESCE(m.content, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.source_type = 'message'
                  AND e.source_id = m.id
                  AND e.model = $1
                  AND e.{column} IS NOT NULL
              )
            ORDER BY m.id DESC
            LIMIT $2::bigint
            "#
        ),
        &[&backend.model, &(remaining as i64)],
    );

    let rows = match rows {
        Ok(r) => r,
        Err(e) => {
            // embeddings table may be missing on a Rust-only core schema
            eprintln!("embed: query skipped: {e}");
            return Ok(summary);
        }
    };
    embed_rows(client, backend, "message", rows, &embed, &mut summary);
    Ok(summary)
}

fn embed_rows<C: GenericClient, F: Fn(&str) -> Result<Vec<f32>>>(
    client: &mut C,
    backend: &EmbeddingBackend,
    source_type: &str,
    rows: Vec<postgres::Row>,
    embed: &F,
    summary: &mut EmbedSummary,
) {
    let column = backend.column;
    for row in rows {
        let id: i64 = row.get(0);
        let text: String = row.get(1);
        match embed(&text) {
            Ok(vector) => {
                let literal = vector_literal(&vector);
                let inserted = client.execute(
                    &format!(
                        r#"
                        INSERT INTO embeddings (source_type, source_id, model, {column})
                        VALUES ($1, $2, $3, $4::text::vector)
                        ON CONFLICT (source_type, source_id, model) DO UPDATE
                        SET {column} = EXCLUDED.{column}
                        "#
                    ),
                    &[&source_type, &id, &backend.model, &literal],
                );
                match inserted {
                    Ok(_) => summary.embedded += 1,
                    Err(e) => {
                        summary.errors += 1;
                        eprintln!("embed: insert {id}: {e}");
                    }
                }
            }
            Err(e) => {
                summary.errors += 1;
                eprintln!("embed: {id}: {e}");
            }
        }
    }
}

/// Cosine distance query for semantic search (requires pgvector).
pub fn semantic_search<C: GenericClient>(
    client: &mut C,
    backend: &EmbeddingBackend,
    query: &str,
    limit: i32,
) -> Result<Vec<(i64, String, f32)>> {
    let vector = embedding::embed_text(backend, query, embedding::TextKind::Query)?;
    let literal = vector_literal(&vector);
    let column = backend.column;
    let rows = client.query(
        &format!(
            r#"
            SELECT e.source_id,
                   left(COALESCE(m.content, ''), 240),
                   (e.{column} <=> $1::text::vector) AS dist
            FROM embeddings e
            JOIN messages m ON m.id = e.source_id AND e.source_type = 'message'
            WHERE e.model = $2
            ORDER BY e.{column} <=> $1::text::vector
            LIMIT $3::bigint
            "#
        ),
        &[&literal, &backend.model, &(limit as i64)],
    )?;
    Ok(rows
        .into_iter()
        .map(|r| {
            let dist: f64 = r.get(2);
            (r.get(0), r.get(1), 1.0 - dist as f32)
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    fn test_db() -> Option<postgres::Client> {
        use postgres::NoTls;
        let host = std::env::var("PGHOST").unwrap_or_else(|_| "localhost".into());
        let user = std::env::var("PGUSER")
            .unwrap_or_else(|_| std::env::var("USERNAME").unwrap_or_else(|_| "postgres".into()));
        let dbname = std::env::var("PGDATABASE").unwrap_or_else(|_| "throughline".into());
        let mut cfg = postgres::Config::new();
        cfg.host(&host)
            .port(
                std::env::var("PGPORT")
                    .ok()
                    .and_then(|p| p.parse().ok())
                    .unwrap_or(5432),
            )
            .user(&user)
            .dbname(&dbname);
        if let Ok(pw) = std::env::var("PGPASSWORD") {
            cfg.password(pw);
        }
        cfg.connect(NoTls).ok()
    }

    fn test_backend() -> EmbeddingBackend {
        EmbeddingBackend {
            name: "test".into(),
            model: format!("test-embed-{}", uuid::Uuid::new_v4()),
            base_url: String::new(),
            column: "embedding_768",
            dim: 768,
            profile: embedding::model_profile("nomic-embed-text").unwrap(),
        }
    }

    fn seed_note<C: GenericClient>(client: &mut C, content: &str, status: &str) -> i64 {
        let category_type = crate::extract_memory::category_column_type(client).unwrap();
        client
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, status) \
                     VALUES ('manual', $1, 'decision'::text::{category_type}, $2) RETURNING id"
                ),
                &[&content, &status],
            )
            .unwrap()
            .get(0)
    }

    fn embedded_rows<C: GenericClient>(client: &mut C, model: &str, source: &str) -> Vec<i64> {
        client
            .query(
                "SELECT source_id FROM embeddings WHERE model = $1 AND source_type = $2 ORDER BY source_id",
                &[&model, &source],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect()
    }

    #[test]
    fn vector_literal_joins_with_commas() {
        assert_eq!(vector_literal(&[0.5, 1.0, -2.0]), "[0.5,1,-2]");
        assert_eq!(vector_literal(&[]), "[]");
    }

    #[test]
    fn embed_pending_embeds_memory_notes_first() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping embed_pending_embeds_memory_notes_first: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let backend = test_backend();
        let tag = uuid::Uuid::new_v4();
        let a = seed_note(&mut tx, &format!("note a {tag}"), "active");
        let b = seed_note(&mut tx, &format!("note b {tag}"), "active");
        let forgotten = seed_note(&mut tx, &format!("note gone {tag}"), "forgotten");
        let conversation_id: i64 = tx
            .query_one(
                "INSERT INTO conversations (session_id, started_at) VALUES ($1, now()) RETURNING id",
                &[&uuid::Uuid::new_v4()],
            )
            .unwrap()
            .get(0);
        let message_id: i64 = tx
            .query_one(
                "INSERT INTO messages (conversation_id, role, content, created_at) \
                 VALUES ($1, 'user'::text::message_role, $2, now()) RETURNING id",
                &[&conversation_id, &format!("message {tag}")],
            )
            .unwrap()
            .get(0);

        let summary = embed_pending_with(&mut tx, &backend, 2, |_| Ok(vec![0.1f32; 768])).unwrap();
        assert_eq!(summary.embedded, 2);
        assert_eq!(summary.errors, 0);
        assert_eq!(
            embedded_rows(&mut tx, &backend.model, "memory_chunk"),
            vec![a, b]
        );
        assert!(!embedded_rows(&mut tx, &backend.model, "memory_chunk").contains(&forgotten));
        assert!(!embedded_rows(&mut tx, &backend.model, "message").contains(&message_id));
    }

    #[test]
    fn embed_pending_skips_notes_already_embedded() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping embed_pending_skips_notes_already_embedded: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let backend = test_backend();
        let tag = uuid::Uuid::new_v4().to_string();
        seed_note(&mut tx, &format!("note a {tag}"), "active");
        seed_note(&mut tx, &format!("note b {tag}"), "active");

        let calls = Cell::new(0);
        let embed = |text: &str| {
            if text.contains(&tag) {
                calls.set(calls.get() + 1);
            }
            Ok(vec![0.1f32; 768])
        };
        embed_pending_with(&mut tx, &backend, 2, embed).unwrap();
        assert_eq!(calls.get(), 2);
        embed_pending_with(&mut tx, &backend, 2, embed).unwrap();
        assert_eq!(calls.get(), 2, "second run must not embed the notes again");
    }

    fn seed_message<C: GenericClient>(client: &mut C, content: &str) -> i64 {
        let conversation_id: i64 = client
            .query_one(
                "INSERT INTO conversations (session_id, started_at) VALUES ($1, now()) RETURNING id",
                &[&uuid::Uuid::new_v4()],
            )
            .unwrap()
            .get(0);
        client
            .query_one(
                "INSERT INTO messages (conversation_id, role, content, created_at) \
                 VALUES ($1, 'user'::text::message_role, $2, now()) RETURNING id",
                &[&conversation_id, &content],
            )
            .unwrap()
            .get(0)
    }

    #[test]
    fn missing_target_does_not_stop_active_embedding() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping missing_target_does_not_stop_active_embedding: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let active = test_backend();
        let note = seed_note(&mut tx, "note for the active model", "active");
        // What resolve_target returns for a model Ollama does not have.
        let target = Err(anyhow::anyhow!(
            "does-not-exist: ollama is missing embedding model does-not-exist"
        ));
        let job =
            embed_job_with(&mut tx, &active, target, 1, |_, _| Ok(vec![0.1f32; 768])).unwrap();
        assert_eq!(job.runs.len(), 1);
        assert_eq!(job.runs[0].1.embedded, 1);
        let err = job.target_error.expect("target error");
        assert!(err.contains("does-not-exist"), "{err}");
        assert!(embedded_rows(&mut tx, &active.model, "memory_chunk").contains(&note));
    }

    #[test]
    fn embed_job_fills_both_models() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping embed_job_fills_both_models: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let active = test_backend();
        let target = test_backend();
        let note = seed_note(&mut tx, "note for both models", "active");
        let job = embed_job_with(&mut tx, &active, Ok(Some(target.clone())), 1, |_, _| {
            Ok(vec![0.1f32; 768])
        })
        .unwrap();
        assert_eq!(job.runs.len(), 2);
        assert!(job.target_error.is_none());
        assert_eq!(job.runs[0].1.embedded, 1);
        assert_eq!(job.runs[1].1.embedded, 1);
        assert!(embedded_rows(&mut tx, &active.model, "memory_chunk").contains(&note));
        assert!(embedded_rows(&mut tx, &target.model, "memory_chunk").contains(&note));
    }

    #[test]
    fn progress_counts_notes_and_messages() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping progress_counts_notes_and_messages: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let backend = test_backend();
        let before = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        assert_eq!((before.notes_done, before.messages_done), (0, 0));
        seed_note(&mut tx, "active one", "active");
        seed_note(&mut tx, "active two", "active");
        seed_note(&mut tx, "gone", "forgotten");
        seed_message(&mut tx, "a message");
        seed_message(&mut tx, "");
        let mid = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        assert_eq!(mid.notes_total, before.notes_total + 2);
        assert_eq!(mid.messages_total, before.messages_total + 1);
        assert_eq!((mid.notes_done, mid.messages_done), (0, 0));
        // Fill exactly the new notes and the new message, newest first.
        embed_pending_with(&mut tx, &backend, 3, |_| Ok(vec![0.1f32; 768])).unwrap();
        let after = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        assert_eq!(after.model, backend.model);
        assert_eq!(after.notes_done, 2);
        assert_eq!(after.messages_done, 1);
    }

    #[test]
    fn superseded_note_is_pending_and_counted() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping superseded_note_is_pending_and_counted: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let backend = test_backend();
        let before = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        let id = seed_note(&mut tx, "old note", "superseded");
        let mid = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        assert_eq!(mid.notes_total, before.notes_total + 1);
        assert_eq!(mid.notes_done, 0);
        let (_, missing) = crate::doctor::embedding_lines(
            &mut tx,
            &crate::access::Actor::local(),
            &backend,
            Ok(None),
        )
        .unwrap();
        assert_ne!(missing, "complete");
        embed_pending_with(&mut tx, &backend, 1, |_| Ok(vec![0.1f32; 768])).unwrap();
        assert!(embedded_rows(&mut tx, &backend.model, "memory_chunk").contains(&id));
        let after = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        assert_eq!(after.notes_done, 1);
        assert_eq!(after.notes_missing(), before.notes_total);
    }

    #[test]
    fn null_status_note_is_embedded() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping null_status_note_is_embedded: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let backend = test_backend();
        let id = seed_note(&mut tx, "no status", "active");
        if tx
            .execute(
                "UPDATE memory_chunks SET status = NULL WHERE id = $1",
                &[&id],
            )
            .is_err()
        {
            eprintln!("skipping null_status_note_is_embedded: status is NOT NULL here");
            return;
        }
        let before = embed_progress(&mut tx, &crate::access::Actor::local(), &backend).unwrap();
        assert_eq!(before.notes_done, 0);
        embed_pending_with(&mut tx, &backend, 1, |_| Ok(vec![0.1f32; 768])).unwrap();
        assert!(embedded_rows(&mut tx, &backend.model, "memory_chunk").contains(&id));
        assert_eq!(
            embed_progress(&mut tx, &crate::access::Actor::local(), &backend)
                .unwrap()
                .notes_done,
            1
        );
    }

    #[test]
    fn null_vector_row_is_filled() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping null_vector_row_is_filled: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let backend = test_backend();
        let id = seed_note(&mut tx, "half embedded", "active");
        if tx
            .execute(
                "INSERT INTO embeddings (source_type, source_id, model) \
                 VALUES ('memory_chunk', $1, $2)",
                &[&id, &backend.model],
            )
            .is_err()
        {
            eprintln!("skipping null_vector_row_is_filled: row without vector not allowed");
            return;
        }
        assert_eq!(
            embed_progress(&mut tx, &crate::access::Actor::local(), &backend)
                .unwrap()
                .notes_done,
            0
        );
        let summary = embed_pending_with(&mut tx, &backend, 1, |_| Ok(vec![0.1f32; 768])).unwrap();
        assert_eq!(summary.embedded, 1);
        assert_eq!(
            embed_progress(&mut tx, &crate::access::Actor::local(), &backend)
                .unwrap()
                .notes_done,
            1
        );
    }

    #[test]
    fn health_counts_only_visible_notes() {
        use crate::access::team_test::{note, two_accounts};
        let Some(mut client) = test_db() else {
            eprintln!("skipping health_counts_only_visible_notes: no PostgreSQL");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let (a, b) = two_accounts(&mut tx);
        let backend = test_backend();
        let private = note(
            &mut tx,
            a.user_id,
            "private",
            "A's private note",
            "insight",
            None,
        );
        note(
            &mut tx,
            a.user_id,
            "private",
            "A's note without a vector",
            "insight",
            None,
        );
        tx.execute(
            "INSERT INTO embeddings (source_type, source_id, model, embedding_768) \
             VALUES ('memory_chunk', $1, $2, $3::text::vector)",
            &[&private, &backend.model, &vector_literal(&[0.1; 768])],
        )
        .expect("embed");
        let (mine, theirs) = (
            embed_progress(&mut tx, &a, &backend).unwrap(),
            embed_progress(&mut tx, &b, &backend).unwrap(),
        );
        // A's two private notes, one with a vector, count for A alone.
        assert_eq!(mine.notes_total, theirs.notes_total + 2);
        assert_eq!(mine.notes_done, theirs.notes_done + 1);
        assert_eq!(mine.notes_missing(), theirs.notes_missing() + 1);
        let line = |tx: &mut postgres::Transaction<'_>, actor: &crate::access::Actor| {
            crate::doctor::embedding_lines(tx, actor, &backend, Ok(Some(backend.clone()))).unwrap()
        };
        let (target_a, _) = line(&mut tx, &a);
        let (target_b, _) = line(&mut tx, &b);
        assert_ne!(
            target_a, target_b,
            "the target line counts A's notes for A only"
        );
    }
}
