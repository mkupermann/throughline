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

/// Embed unembedded rows for `source_type` in ('message','conversation','memory_chunk').
pub fn embed_pending<C: GenericClient>(
    client: &mut C,
    backend: &EmbeddingBackend,
    limit: usize,
) -> Result<EmbedSummary> {
    let mut summary = EmbedSummary::default();
    let column = backend.column;

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
        &[&backend.model, &(limit as i64)],
    );

    let rows = match rows {
        Ok(r) => r,
        Err(e) => {
            // embeddings table may be missing on a Rust-only core schema
            eprintln!("embed: query skipped: {e}");
            return Ok(summary);
        }
    };

    for row in rows {
        let id: i64 = row.get(0);
        let text: String = row.get(1);
        match embedding::embed_text(backend, &text) {
            Ok(vector) => {
                let literal = format!(
                    "[{}]",
                    vector
                        .iter()
                        .map(|v| v.to_string())
                        .collect::<Vec<_>>()
                        .join(",")
                );
                let inserted = client.execute(
                    &format!(
                        r#"
                        INSERT INTO embeddings (source_type, source_id, model, {column})
                        VALUES ('message', $1, $2, $3::text::vector)
                        ON CONFLICT (source_type, source_id, model) DO UPDATE
                        SET {column} = EXCLUDED.{column}
                        "#
                    ),
                    &[&id, &backend.model, &literal],
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
    Ok(summary)
}

/// Cosine distance query for semantic search (requires pgvector).
pub fn semantic_search<C: GenericClient>(
    client: &mut C,
    backend: &EmbeddingBackend,
    query: &str,
    limit: i32,
) -> Result<Vec<(i64, String, f32)>> {
    let vector = embedding::embed_text(backend, query)?;
    let literal = format!(
        "[{}]",
        vector
            .iter()
            .map(|v| v.to_string())
            .collect::<Vec<_>>()
            .join(",")
    );
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
