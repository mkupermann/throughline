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
/// Forgotten notes are skipped; superseded ones keep their vector.
pub fn embed_pending_with<C: GenericClient, F: Fn(&str) -> Result<Vec<f32>>>(
    client: &mut C,
    backend: &EmbeddingBackend,
    limit: usize,
    embed: F,
) -> Result<EmbedSummary> {
    let mut summary = EmbedSummary::default();
    let notes = client.query(
        r#"
            SELECT c.id, left(c.content, 4000)
            FROM memory_chunks c
            WHERE c.status <> 'forgotten'
              AND COALESCE(c.content, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.source_type = 'memory_chunk'
                  AND e.source_id = c.id
                  AND e.model = $1
              )
            ORDER BY c.id DESC
            LIMIT $2::bigint
            "#,
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
        r#"
            SELECT m.id, left(COALESCE(m.content, ''), 4000)
            FROM messages m
            WHERE COALESCE(m.content, '') <> ''
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.source_type = 'message'
                  AND e.source_id = m.id
                  AND e.model = $1
              )
            ORDER BY m.id DESC
            LIMIT $2::bigint
            "#,
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
}
