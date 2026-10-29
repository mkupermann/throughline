//! Idempotent write path: upsert conversations, replace messages, ledger.

use crate::types::{NormalisedConversation, NormalisedMessage};
use anyhow::{Context, Result};
use postgres::GenericClient;
use std::path::Path;

/// Strip U+0000 so PostgreSQL text/jsonb columns accept the value.
pub fn scrub_nul(value: &str) -> String {
    value.replace('\0', "")
}

pub fn scrub_nul_json(value: &serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::String(s) => serde_json::Value::String(scrub_nul(s)),
        serde_json::Value::Array(a) => {
            serde_json::Value::Array(a.iter().map(scrub_nul_json).collect())
        }
        serde_json::Value::Object(o) => {
            let mut out = serde_json::Map::new();
            for (k, v) in o {
                out.insert(k.clone(), scrub_nul_json(v));
            }
            serde_json::Value::Object(out)
        }
        other => other.clone(),
    }
}

pub fn upsert_conversation<C: GenericClient>(
    client: &mut C,
    conv: &NormalisedConversation,
) -> Result<i64> {
    let row = client
        .query_one(
            r#"
        INSERT INTO conversations
            (session_id, project_path, model, entrypoint, git_branch,
             started_at, ended_at, message_count,
             token_count_in, token_count_out, summary, metadata, source_tool)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (session_id) DO UPDATE
        SET project_path    = EXCLUDED.project_path,
            model           = EXCLUDED.model,
            entrypoint      = EXCLUDED.entrypoint,
            git_branch      = EXCLUDED.git_branch,
            started_at      = EXCLUDED.started_at,
            ended_at        = EXCLUDED.ended_at,
            message_count   = EXCLUDED.message_count,
            token_count_in  = EXCLUDED.token_count_in,
            token_count_out = EXCLUDED.token_count_out,
            summary         = COALESCE(EXCLUDED.summary, conversations.summary),
            metadata        = EXCLUDED.metadata,
            source_tool     = EXCLUDED.source_tool,
            updated_at      = NOW()
        RETURNING id
        "#,
            &[
                &conv.session_id,
                &conv.project_path,
                &conv.model,
                &conv.entrypoint,
                &conv.git_branch,
                &conv.started_at,
                &conv.ended_at,
                &(conv.messages.len() as i32),
                &conv.token_count_in,
                &conv.token_count_out,
                &conv.summary,
                &conv.metadata,
                &conv.source_tool,
            ],
        )
        .context("upsert conversation")?;
    Ok(row.get(0))
}

/// Write a conversation's messages. When every message carries a uuid, only
/// new ones are inserted: existing rows keep their ids, so their embeddings,
/// favorites and checkpoints stay attached. Without uuids there is no stable
/// key, so the conversation's messages are replaced.
pub fn replace_messages<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
    conv: &NormalisedConversation,
) -> Result<usize> {
    if conv.messages.iter().all(|m| m.uuid.is_some()) {
        for m in &conv.messages {
            insert_message(client, conversation_id, m, conv)?;
        }
        return Ok(conv.messages.len());
    }

    // embeddings may be absent on a core-only schema. A failed statement aborts
    // the surrounding transaction, so probe first (to_regclass) and skip.
    let has_embeddings: bool = client
        .query_one("SELECT to_regclass('public.embeddings') IS NOT NULL", &[])
        .map(|r| r.get(0))
        .unwrap_or(false);
    if has_embeddings {
        client.execute(
            "DELETE FROM embeddings WHERE source_type = 'message' AND source_id IN \
             (SELECT id FROM messages WHERE conversation_id = $1)",
            &[&conversation_id],
        )?;
    }
    client.execute(
        "DELETE FROM messages WHERE conversation_id = $1",
        &[&conversation_id],
    )?;

    if conv.messages.is_empty() {
        return Ok(0);
    }

    for m in &conv.messages {
        insert_message(client, conversation_id, m, conv)?;
    }
    Ok(conv.messages.len())
}

fn insert_message<C: GenericClient>(
    client: &mut C,
    conversation_id: i64,
    m: &NormalisedMessage,
    conv: &NormalisedConversation,
) -> Result<()> {
    let content = scrub_nul(&m.content);
    let content_blocks = m.content_blocks.as_ref().map(scrub_nul_json);
    let tool_calls = m.tool_calls.as_ref().map(scrub_nul_json);
    let metadata = scrub_nul_json(&m.metadata);
    let created_at = m.created_at.unwrap_or(conv.started_at);
    let role = m.role.as_str();

    client
        .execute(
            r#"
        INSERT INTO messages
            (conversation_id, uuid, parent_uuid, role, content,
             content_blocks, tool_calls, tool_name, is_sidechain,
             model, token_count, created_at, metadata)
        VALUES ($1, $2, $3, $4::text::message_role, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (conversation_id, uuid) WHERE uuid IS NOT NULL DO NOTHING
        "#,
            &[
                &conversation_id,
                &m.uuid,
                &m.parent_uuid,
                &role,
                &content,
                &content_blocks,
                &tool_calls,
                &m.tool_name,
                &m.is_sidechain,
                &m.model,
                &m.token_count,
                &created_at,
                &metadata,
            ],
        )
        .context("insert message")?;
    Ok(())
}

pub fn backfill_projects<C: GenericClient>(client: &mut C) -> Result<u64> {
    let inserted = client.execute(
        r#"
        INSERT INTO projects (name, status)
        SELECT DISTINCT project_name, 'active'
        FROM conversations
        WHERE project_name IS NOT NULL AND project_name <> ''
        ON CONFLICT (name) DO NOTHING
        "#,
        &[],
    )?;
    Ok(inserted)
}

/// Record a deliberate decline so the file is not re-parsed every run.
pub fn record_decision<C: GenericClient>(
    client: &mut C,
    path: &Path,
    file_hash: &str,
) -> Result<()> {
    client.execute(
        "INSERT INTO ingestion_log (file_path, file_hash, record_count) \
         VALUES ($1, $2, 0) ON CONFLICT DO NOTHING",
        &[&path.to_string_lossy().to_string(), &file_hash],
    )?;
    Ok(())
}

pub fn already_ingested<C: GenericClient>(
    client: &mut C,
    path: &Path,
    file_hash: &str,
) -> Result<Option<i32>> {
    let rows = client.query(
        "SELECT record_count FROM ingestion_log WHERE file_path = $1 AND file_hash = $2",
        &[&path.to_string_lossy().to_string(), &file_hash],
    )?;
    Ok(rows.first().map(|r| r.get(0)))
}

pub fn record_ingest<C: GenericClient>(
    client: &mut C,
    path: &Path,
    file_hash: &str,
    record_count: usize,
) -> Result<()> {
    client.execute(
        "INSERT INTO ingestion_log (file_path, file_hash, record_count) \
         VALUES ($1, $2, $3) \
         ON CONFLICT (file_path, file_hash) DO UPDATE SET record_count = EXCLUDED.record_count",
        &[
            &path.to_string_lossy().to_string(),
            &file_hash,
            &(record_count as i32),
        ],
    )?;
    Ok(())
}

pub fn was_seen_before<C: GenericClient>(client: &mut C, path: &Path) -> Result<bool> {
    let rows = client.query(
        "SELECT 1 FROM ingestion_log WHERE file_path = $1 LIMIT 1",
        &[&path.to_string_lossy().to_string()],
    )?;
    Ok(!rows.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Connect to the database (`PG*` env). Skip when unreachable so offline
    /// `cargo test --lib` stays green. Mirrors the server.rs test helper.
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

    fn conversation(
        session: uuid::Uuid,
        summary: Option<&str>,
        messages: &[(Option<uuid::Uuid>, &str)],
    ) -> NormalisedConversation {
        let at = chrono::DateTime::from_timestamp(4_070_908_800, 0).unwrap();
        NormalisedConversation {
            session_id: session,
            project_path: Some("/tmp/writer-test".into()),
            model: None,
            entrypoint: None,
            git_branch: None,
            started_at: at,
            ended_at: None,
            messages: messages
                .iter()
                .map(|(uuid, text)| NormalisedMessage {
                    role: "user".into(),
                    content: text.to_string(),
                    uuid: *uuid,
                    created_at: Some(at),
                    metadata: json!({}),
                    ..Default::default()
                })
                .collect(),
            token_count_in: None,
            token_count_out: None,
            summary: summary.map(String::from),
            metadata: json!({}),
            source_tool: Some("claude_code".into()),
        }
    }

    fn message_ids<C: GenericClient>(client: &mut C, conversation_id: i64) -> Vec<i64> {
        client
            .query(
                "SELECT id FROM messages WHERE conversation_id = $1 ORDER BY id",
                &[&conversation_id],
            )
            .unwrap()
            .iter()
            .map(|r| r.get(0))
            .collect()
    }

    #[test]
    fn rewrite_keeps_message_ids_embeddings_and_title() {
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping rewrite_keeps_message_ids_embeddings_and_title: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let session = uuid::Uuid::new_v4();
        let (a, b, c) = (
            uuid::Uuid::new_v4(),
            uuid::Uuid::new_v4(),
            uuid::Uuid::new_v4(),
        );
        let first = conversation(
            session,
            Some("generated title"),
            &[(Some(a), "one"), (Some(b), "two")],
        );
        let id = upsert_conversation(&mut tx, &first).unwrap();
        replace_messages(&mut tx, id, &first).unwrap();
        let before = message_ids(&mut tx, id);
        tx.execute(
            "INSERT INTO embeddings (source_type, source_id, model) VALUES ('message', $1, 'test')",
            &[&before[0]],
        )
        .unwrap();

        // The session file grew by one message; adapters send no title.
        let grown = conversation(
            session,
            None,
            &[(Some(a), "one"), (Some(b), "two"), (Some(c), "three")],
        );
        assert_eq!(upsert_conversation(&mut tx, &grown).unwrap(), id);
        replace_messages(&mut tx, id, &grown).unwrap();
        let after = message_ids(&mut tx, id);
        assert_eq!(after.len(), 3);
        assert_eq!(after[..2], before[..], "existing messages keep their ids");
        let embeddings: i64 = tx
            .query_one(
                "SELECT count(*) FROM embeddings WHERE source_type = 'message' AND source_id = $1",
                &[&before[0]],
            )
            .unwrap()
            .get(0);
        assert_eq!(
            embeddings, 1,
            "the embedding of an unchanged message survives"
        );
        let summary: Option<String> = tx
            .query_one("SELECT summary FROM conversations WHERE id = $1", &[&id])
            .unwrap()
            .get(0);
        assert_eq!(summary.as_deref(), Some("generated title"));
    }

    #[test]
    fn rewrite_without_uuids_does_not_duplicate() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping rewrite_without_uuids_does_not_duplicate: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let conv = conversation(uuid::Uuid::new_v4(), None, &[(None, "one"), (None, "two")]);
        let id = upsert_conversation(&mut tx, &conv).unwrap();
        replace_messages(&mut tx, id, &conv).unwrap();
        replace_messages(&mut tx, id, &conv).unwrap();
        assert_eq!(message_ids(&mut tx, id).len(), 2);
    }

    #[test]
    fn scrub_nul_drops_only_nul_bytes() {
        assert_eq!(scrub_nul("ok\0text"), "oktext");
        assert_eq!(scrub_nul("clean"), "clean");
    }

    #[test]
    fn scrub_nul_json_recurses() {
        let v = json!({"a": "x\0y", "b": ["p\0q", 1], "c": {"d": "e\0"}});
        let out = scrub_nul_json(&v);
        assert_eq!(out["a"], "xy");
        assert_eq!(out["b"][0], "pq");
        assert_eq!(out["c"]["d"], "e");
        assert_eq!(out["b"][1], 1);
    }
}
