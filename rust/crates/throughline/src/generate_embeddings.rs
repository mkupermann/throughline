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

/// A note's vector from the active model.
pub struct NoteVector {
    pub model: String,
    pub column: &'static str,
    pub vector: Vec<f32>,
}

/// `content` embedded as a document with the active model, within
/// [`embedding::QUICK_EMBED_TIMEOUT`]. `None` while `breaker` is open or
/// when the call fails; see [`note_vector_with`] for what is remembered.
pub fn note_vector(content: &str, breaker: &embedding::EmbedBreaker) -> Option<NoteVector> {
    #[cfg(test)]
    if let Some(v) = write_fixture::vector_for(content) {
        return Some(v);
    }
    let text = content.to_string();
    note_vector_with(breaker, embedding::now_ms(), move || {
        embedding::within(embedding::QUICK_EMBED_TIMEOUT, move || {
            let backend = embedding::resolve()?;
            let vector = embedding::embed_text(&backend, &text, embedding::TextKind::Document)?;
            check_note_vector(&vector, backend.dim)?;
            Ok(NoteVector {
                model: backend.model,
                column: backend.column,
                vector,
            })
        })
    })
}

/// A vector Ollama gave that the insert would refuse.
#[derive(Debug)]
pub(crate) struct RejectedVector(String);

impl std::fmt::Display for RejectedVector {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RejectedVector {}

/// A vector the insert can take: `dim` values, all finite. pgvector refuses
/// NaN and infinity, and a refused insert would fail the caller's
/// transaction.
pub(crate) fn check_note_vector(vector: &[f32], dim: usize) -> anyhow::Result<()> {
    if vector.len() != dim {
        return Err(
            RejectedVector(format!("{} values, the column takes {dim}", vector.len())).into(),
        );
    }
    if let Some(i) = vector.iter().position(|x| !x.is_finite()) {
        return Err(RejectedVector(format!("value {i} is {}", vector[i])).into());
    }
    Ok(())
}

/// [`note_vector`] with the clock and the embedder passed in. A failure is
/// logged; only one that says Ollama is down or slow (unreachable, missing
/// model, an HTTP error, no answer in time) opens `breaker`. A refusal by
/// the remote-model lock or a refused vector does not, so the next default
/// recall still asks and answers as it should.
pub(crate) fn note_vector_with(
    breaker: &embedding::EmbedBreaker,
    now: i64,
    embed: impl FnOnce() -> anyhow::Result<NoteVector>,
) -> Option<NoteVector> {
    if breaker.is_open(now) {
        return None;
    }
    match embed() {
        Ok(v) => Some(v),
        Err(e) => {
            eprintln!("embed at write: {e:#}");
            let refused = e
                .downcast_ref::<crate::llm::RemoteLlmNotAllowed>()
                .is_some()
                || e.downcast_ref::<RejectedVector>().is_some();
            if !refused {
                breaker.trip(now);
            }
            None
        }
    }
}

/// Gives note `id` a vector of the active model right after it was written,
/// so it ranks by meaning at once. The write never fails for it: no Ollama,
/// a missing model, a refusal, a timeout or a failed insert is logged, and
/// the `embed` job fills the gap later. A note that already has a vector of
/// the active model, a forgotten or an empty note is left alone. Uses the
/// process breaker, [`embedding::BREAKER`].
pub fn embed_written_note<C: GenericClient>(client: &mut C, id: i64) {
    embed_written_note_with(client, id, &embedding::BREAKER)
}

const NOTE_VECTOR_SAVEPOINT: &str = "throughline_note_vector";

/// [`embed_written_note`] with the breaker passed in. Inside a transaction
/// block its statements run under a savepoint of their own, so an error
/// rolls back only them and never aborts the caller's transaction.
/// `client.transaction()` would do the same for a `Transaction`, but on a
/// plain client inside a `BEGIN` it would end the caller's block.
///
/// Callers call it right after writing the note on the same connection.
/// Inside a block that write gave the transaction an id; in autocommit the
/// write's own transaction has ended, and the check below runs in a fresh
/// one without an id. (A timestamp comparison does not work: under the
/// extended protocol `statement_timestamp()` moves on at Execute even in
/// autocommit.) Passing the answer in from every caller would mean a flag
/// on `supersede_memory`, `keep_memory`, `consolidate` and MCP `write`,
/// which run both ways and cannot tell which.
pub fn embed_written_note_with<C: GenericClient>(
    client: &mut C,
    id: i64,
    breaker: &embedding::EmbedBreaker,
) {
    let in_block = match client.query_one("SELECT txid_current_if_assigned() IS NOT NULL", &[]) {
        Ok(row) => row.get::<_, bool>(0),
        Err(e) => {
            eprintln!("embed at write: note {id}: {e}");
            return;
        }
    };
    if in_block {
        if let Err(e) = client.batch_execute(&format!("SAVEPOINT {NOTE_VECTOR_SAVEPOINT}")) {
            eprintln!("embed at write: note {id}: {e}");
            return;
        }
    }
    let result = store_note_vector(client, id, breaker);
    if in_block {
        let end = if result.is_ok() {
            format!("RELEASE SAVEPOINT {NOTE_VECTOR_SAVEPOINT}")
        } else {
            format!(
                "ROLLBACK TO SAVEPOINT {NOTE_VECTOR_SAVEPOINT}; \
                 RELEASE SAVEPOINT {NOTE_VECTOR_SAVEPOINT}"
            )
        };
        if let Err(e) = client.batch_execute(&end) {
            eprintln!("embed at write: note {id}: {e}");
        }
    }
    if let Err(e) = result {
        eprintln!("embed at write: note {id}: {e}");
    }
}

/// The SELECT, the embedding and the INSERT of [`embed_written_note_with`].
fn store_note_vector<C: GenericClient>(
    client: &mut C,
    id: i64,
    breaker: &embedding::EmbedBreaker,
) -> Result<(), postgres::Error> {
    let model = embedding::configured_model();
    let row = client.query_opt(
        "SELECT left(mc.content, 4000) FROM memory_chunks mc \
         WHERE mc.id = $1 AND COALESCE(mc.status, 'active') <> 'forgotten' \
           AND COALESCE(mc.content, '') <> '' \
           AND NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.source_type = 'memory_chunk' \
                           AND e.source_id = mc.id AND e.model = $2)",
        &[&id, &model],
    )?;
    let Some(row) = row else {
        return Ok(());
    };
    let text: String = row.get(0);
    let Some(v) = note_vector(&text, breaker) else {
        return Ok(());
    };
    let column = v.column;
    client.execute(
        &format!(
            "INSERT INTO embeddings (source_type, source_id, model, {column}) \
             VALUES ('memory_chunk', $1, $2, $3::text::vector) \
             ON CONFLICT (source_type, source_id, model) DO UPDATE SET {column} = EXCLUDED.{column}"
        ),
        &[&id, &v.model, &vector_literal(&v.vector)],
    )?;
    Ok(())
}

/// Test notes get a vector at write time without Ollama.
#[cfg(test)]
pub(crate) mod write_fixture {
    use std::sync::Mutex;

    type Fixture = (String, String, Option<Vec<f32>>);
    static FIXTURES: Mutex<Vec<Fixture>> = Mutex::new(Vec::new());

    /// While `f` runs, a note whose text holds `tag` gets the vector
    /// `[1, 0, …]` of `model` when it is written. Other notes are untouched.
    pub(crate) fn with<T>(tag: &str, model: &str, f: impl FnOnce() -> T) -> T {
        with_fixture(tag, model, None, f)
    }

    /// [`with`], with `vector` as it is, unchecked: a test can hand the
    /// insert a vector it refuses.
    pub(crate) fn with_vector<T>(
        tag: &str,
        model: &str,
        vector: Vec<f32>,
        f: impl FnOnce() -> T,
    ) -> T {
        with_fixture(tag, model, Some(vector), f)
    }

    fn with_fixture<T>(
        tag: &str,
        model: &str,
        vector: Option<Vec<f32>>,
        f: impl FnOnce() -> T,
    ) -> T {
        struct Pop(String);
        impl Drop for Pop {
            fn drop(&mut self) {
                FIXTURES
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .retain(|(t, _, _)| *t != self.0);
            }
        }
        FIXTURES.lock().unwrap_or_else(|e| e.into_inner()).push((
            tag.to_string(),
            model.to_string(),
            vector,
        ));
        let _pop = Pop(tag.to_string());
        f()
    }

    pub(crate) fn vector_for(content: &str) -> Option<super::NoteVector> {
        let fixtures = FIXTURES.lock().unwrap_or_else(|e| e.into_inner());
        let (_, model, given) = fixtures
            .iter()
            .find(|(t, _, _)| content.contains(t.as_str()))?;
        let vector = given.clone().unwrap_or_else(|| {
            let mut v = vec![0.0; 768];
            v[0] = 1.0;
            v
        });
        Some(super::NoteVector {
            model: model.clone(),
            column: "embedding_768",
            vector,
        })
    }
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

    #[test]
    fn write_time_embed_failure_does_not_abort_the_callers_transaction() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping write_time_embed_failure_does_not_abort_the_callers_transaction: no PostgreSQL reachable");
            return;
        };
        // Everything rolls back with this one at the end.
        let mut outer = client.transaction().unwrap();
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let model = format!("test-embed-fail-{tag}");
        let conv: i64 = outer
            .query_one(
                "INSERT INTO conversations (session_id, project_path, started_at) \
                 VALUES ($1, '/fictional/embed-fail', now()) RETURNING id",
                &[&uuid::Uuid::new_v4()],
            )
            .unwrap()
            .get(0);
        // Three values for a 768 column: the insert of the vector fails.
        let id = write_fixture::with_vector(&tag, &model, vec![1.0, 0.0, 0.0], || {
            // The caller's own transaction, like an extraction window.
            let mut window = outer.transaction().unwrap();
            let item = crate::extract_memory::ExtractedMemory {
                content: format!("note {tag}"),
                category: "insight".into(),
                tags: vec![],
                confidence: 0.8,
            };
            let id =
                crate::extract_memory::insert_memory(&mut window, conv, &item, chrono::Utc::now())
                    .expect("the note is written");
            // The window's next statement runs and the window commits.
            window
                .execute(
                    "UPDATE conversations SET memory_extracted_until = now() WHERE id = $1",
                    &[&conv],
                )
                .expect("the window is not aborted");
            window.commit().expect("the window commits");
            id
        });
        let kept: i64 = outer
            .query_one("SELECT count(*) FROM memory_chunks WHERE id = $1", &[&id])
            .unwrap()
            .get(0);
        assert_eq!(kept, 1, "the note survives the failed vector");
        assert!(embedded_rows(&mut outer, &model, "memory_chunk").is_empty());
    }

    #[test]
    fn embed_written_note_runs_in_autocommit() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping embed_written_note_runs_in_autocommit: no PostgreSQL reachable");
            return;
        };
        let tag = uuid::Uuid::new_v4().simple().to_string();
        let model = format!("test-embed-auto-{tag}");
        // A plain client without BEGIN, as the server's: each statement
        // commits on its own, so the test takes its rows out again.
        let id = seed_note(&mut client, &format!("auto {tag}"), "active");
        write_fixture::with(&tag, &model, || embed_written_note(&mut client, id));
        let rows = embedded_rows(&mut client, &model, "memory_chunk");
        client
            .execute("DELETE FROM embeddings WHERE model = $1", &[&model])
            .unwrap();
        client
            .execute("DELETE FROM memory_chunks WHERE id = $1", &[&id])
            .unwrap();
        assert_eq!(rows, vec![id], "the note got its vector in autocommit");
    }

    #[test]
    fn note_vector_rejects_non_finite_values() {
        let mut v = vec![0.0f32; 768];
        assert!(check_note_vector(&v, 768).is_ok());
        for bad in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
            v[3] = bad;
            assert!(check_note_vector(&v, 768).is_err(), "{bad}");
        }
        assert!(check_note_vector(&[0.0; 767], 768).is_err());
        // A refused vector is not Ollama being down: the breaker stays shut.
        let breaker = embedding::EmbedBreaker::new();
        let got = note_vector_with(&breaker, 1_000, || {
            check_note_vector(&[f32::NAN], 1)?;
            unreachable!("a NaN never passes")
        });
        assert!(got.is_none());
        assert!(!breaker.is_open(1_001));
    }

    #[test]
    fn refused_write_does_not_open_the_breaker() {
        let t0 = 1_000_000;
        let asks = |breaker: &embedding::EmbedBreaker| {
            let asked = Cell::new(false);
            let _ = crate::recall::question_vector_with(
                crate::recall::RecallMode::Current,
                None,
                breaker,
                t0 + 1,
                || {
                    asked.set(true);
                    Ok(None)
                },
                || unreachable!("the default rank takes the quick path"),
            );
            asked.get()
        };
        // A write refused by the remote-model lock is logged, not remembered,
        // so the next default recall still asks and gets its refusal.
        let breaker = embedding::EmbedBreaker::new();
        let got = note_vector_with(&breaker, t0, || {
            Err(anyhow::Error::new(crate::llm::RemoteLlmNotAllowed {
                label: "ollama/x".into(),
            }))
        });
        assert!(got.is_none());
        assert!(!breaker.is_open(t0 + 1));
        assert!(asks(&breaker));
        // An unreachable Ollama on a write is remembered, for queries too.
        let shared = embedding::EmbedBreaker::new();
        let got = note_vector_with(&shared, t0, || {
            Err(anyhow::anyhow!("ollama unreachable at http://127.0.0.1:1"))
        });
        assert!(got.is_none());
        assert!(shared.is_open(t0 + 1));
        assert!(!asks(&shared));
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
