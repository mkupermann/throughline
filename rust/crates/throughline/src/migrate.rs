//! Versioned schema migrations for the ingest core.
//!
//! Compatible with the Python schema where they overlap (`conversations`,
//! `messages`, `ingestion_log`, `projects`): same column names, same unique
//! keys, so a corpus written by either side stays readable. The full Python
//! schema (memory_chunks, embeddings, pm_*) is intentionally not recreated
//! here; this pass is ingest + search.

use anyhow::{Context, Result};
use postgres::Client;

const CORE_SCHEMA: &str = r#"
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

DO $$ BEGIN
    CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system', 'tool_result');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS conversations (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          UUID NOT NULL UNIQUE,
    generated_by        TEXT,
    project_path        TEXT,
    project_name        TEXT GENERATED ALWAYS AS (
        CASE
            WHEN project_path IS NULL THEN 'unknown'
            ELSE split_part(replace(project_path, '\', '/'), '/', -1)
        END
    ) STORED,
    model               TEXT,
    entrypoint          TEXT,
    git_branch          TEXT,
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    message_count       INTEGER DEFAULT 0,
    token_count_in      BIGINT,
    token_count_out     BIGINT,
    cost_usd            NUMERIC(10,4),
    summary             TEXT,
    tags                TEXT[] DEFAULT '{}',
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    source_tool         TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    uuid            UUID,
    parent_uuid     UUID,
    role            message_role NOT NULL,
    content         TEXT,
    content_blocks  JSONB,
    tool_calls      JSONB,
    tool_name       TEXT,
    token_count     BIGINT,
    is_sidechain    BOOLEAN DEFAULT false,
    model           TEXT,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL,
    metadata        JSONB DEFAULT '{}'
);

-- Arbiter for writer.rs ON CONFLICT (conversation_id, uuid) WHERE uuid IS NOT NULL.
-- Postgres matches the ON CONFLICT spec to an index by exact columns + predicate;
-- a unique index on uuid alone does not match and inserts fail with SQLSTATE 42P10.
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_conversation_uuid
    ON messages (conversation_id, uuid) WHERE uuid IS NOT NULL;
DROP INDEX IF EXISTS messages_uuid_key;
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_content_trgm
    ON messages USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_conversations_session
    ON conversations (session_id);

CREATE TABLE IF NOT EXISTS ingestion_log (
    id           BIGSERIAL PRIMARY KEY,
    file_path    TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    ingested_at  TIMESTAMPTZ DEFAULT now(),
    record_count INTEGER,
    UNIQUE (file_path, file_hash)
);

CREATE TABLE IF NOT EXISTS projects (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    contacts    JSONB DEFAULT '[]',
    decisions   JSONB DEFAULT '[]',
    status      TEXT DEFAULT 'active',
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Minimal companions so writer/search/extract do not abort transactions
-- on a core-only install. Full Python schema can still exist alongside.
CREATE TABLE IF NOT EXISTS embeddings (
    id              BIGSERIAL PRIMARY KEY,
    source_type     TEXT NOT NULL,
    source_id       BIGINT NOT NULL,
    model           TEXT NOT NULL,
    embedding_768   vector(768),
    embedding_1536  vector(1536),
    dim             INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_type, source_id, model)
);

CREATE TABLE IF NOT EXISTS memory_chunks (
    id              BIGSERIAL PRIMARY KEY,
    source_type     TEXT NOT NULL DEFAULT 'manual',
    source_id       BIGINT,
    content         TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'insight',
    tags            TEXT[] DEFAULT '{}',
    confidence      REAL DEFAULT 0.5,
    project_name    TEXT,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    superseded_by   BIGINT,
    superseded_at   TIMESTAMPTZ,
    status          TEXT DEFAULT 'active',
    merged_from     BIGINT[],
    access_count    INTEGER DEFAULT 0,
    last_accessed   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS prompts (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL DEFAULT 'project_instruction',
    content     TEXT NOT NULL,
    variables   JSONB DEFAULT '[]',
    source_path TEXT,
    usage_count INTEGER DEFAULT 0,
    tags        TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
"#;

/// Fix the message-uuid arbiter index for databases that already ran 001
/// before the ON CONFLICT spec was corrected to (conversation_id, uuid).
const UUID_ARBITER: &str = r#"
CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_conversation_uuid
    ON messages (conversation_id, uuid) WHERE uuid IS NOT NULL;
DROP INDEX IF EXISTS messages_uuid_key;
"#;

/// Migrations the Rust CLI owns. The Python package has its own numbered
/// files; both share `applied_migrations` so a mixed-history database works.
const TOKEN_BIGINT: &str = r#"
ALTER TABLE conversations ALTER COLUMN token_count_in TYPE BIGINT;
ALTER TABLE conversations ALTER COLUMN token_count_out TYPE BIGINT;
"#;

/// Migrations the Rust CLI owns. The Python package has its own numbered
/// files; both share `applied_migrations` so a mixed-history database works.
/// Re-drop the legacy uuid-only index for DBs that already recorded 002
/// before it included the DROP.
const DROP_UUID_ONLY_INDEX: &str = r#"
DROP INDEX IF EXISTS messages_uuid_key;
"#;

const MSG_TOKEN_BIGINT: &str = r#"
ALTER TABLE messages ALTER COLUMN token_count TYPE BIGINT;
"#;

/// `memory_chunks.updated_at` is written by MCP `forget` (status change to
/// 'forgotten'). Fresh core-only installs created by 001 have no such column,
/// so `forget` failed against a new schema. Idempotent: `IF NOT EXISTS` keeps
/// a second run a no-op even if the ledger entry is missing.
const MEMORY_CHUNKS_UPDATED_AT: &str = r#"
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
"#;

/// Zeitgrundsatz: source memory source time (`source_at` = time of the source
/// itself: session end for extracted notes, file mtime for agent memory, user
/// action time for pins/MCP/manual notes) and separate the instruction-file
/// scan time (`scanned_at`) from the file's own modification time.
/// `messages.created_at` / `conversations.started_at` carry no DEFAULT in the
/// Rust schema, so no ALTER ... DROP DEFAULT is needed here.
/// `memory_chunks.source_id` is BIGINT, matching `conversations.id` — the
/// backfill join needs no cast.
const SOURCE_AT: &str = r#"
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS source_at TIMESTAMPTZ;
UPDATE memory_chunks mc SET source_at = c.ended_at FROM conversations c WHERE mc.source_type = 'conversation' AND mc.source_id = c.id AND mc.source_at IS NULL;
UPDATE memory_chunks SET source_at = created_at WHERE source_at IS NULL;
ALTER TABLE prompts ADD COLUMN IF NOT EXISTS scanned_at TIMESTAMPTZ;
UPDATE prompts SET scanned_at = updated_at WHERE scanned_at IS NULL;
"#;

/// AI-assistant configuration inventory: masked content + SHA-256 per file.
const ASSISTANT_CONFIGS: &str = r#"
CREATE TABLE IF NOT EXISTS assistant_configs (
    id BIGSERIAL PRIMARY KEY,
    tool TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    project TEXT,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    bytes BIGINT NOT NULL,
    modified_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    present BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (tool, path)
);
CREATE INDEX IF NOT EXISTS assistant_configs_kind_idx ON assistant_configs (tool, kind);
"#;

/// Favorites: one starred item per row. `ref` is the item identifier as text —
/// database id for conversations / messages / memory, project name for
/// projects, inventory hash for imported configs.
const FAVORITES: &str = r#"
CREATE TABLE IF NOT EXISTS favorites (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('conversation','message','memory','project','config')),
    ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, ref)
);
"#;

/// Memory-extraction watermark: messages at or before it were already sent to
/// the model, so `extract-memory` only sends what came after. Upserts on
/// re-ingest leave the column alone. Conversations that already carry
/// extracted notes start at their latest note, so the first run after this
/// migration does not extract them a second time.
const MEMORY_EXTRACTED_UNTIL: &str = r#"
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS memory_extracted_until TIMESTAMPTZ;
UPDATE conversations c SET memory_extracted_until = n.last_note
FROM (SELECT source_id, max(created_at) AS last_note FROM memory_chunks
      WHERE source_type = 'conversation' GROUP BY source_id) n
WHERE n.source_id = c.id AND c.memory_extracted_until IS NULL;
"#;

/// Extend `memory_chunks_source_type_check` with `'ui_pin'` when the
/// constraint exists (Python-era schema). No-op on a fresh Rust schema
/// (TEXT column, no CHECK).
const MEMORY_SOURCE_UI_PIN: &str = r#"
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'memory_chunks_source_type_check') THEN
        ALTER TABLE memory_chunks DROP CONSTRAINT memory_chunks_source_type_check;
        ALTER TABLE memory_chunks ADD CONSTRAINT memory_chunks_source_type_check
            CHECK (source_type IN ('conversation','manual','mcp_write','reflection_merge','consolidation','ui_pin'));
    END IF;
END $$;
"#;

/// Migrations the Rust CLI owns. The Python package has its own numbered
/// files; both share `applied_migrations` so a mixed-history database works.
const MIGRATIONS: &[(&str, &str)] = &[
    ("001_rust_core.sql", CORE_SCHEMA),
    ("002_uuid_arbiter.sql", UUID_ARBITER),
    ("003_token_bigint.sql", TOKEN_BIGINT),
    ("004_drop_messages_uuid_key.sql", DROP_UUID_ONLY_INDEX),
    ("005_message_token_bigint.sql", MSG_TOKEN_BIGINT),
    ("006_memory_chunks_updated_at.sql", MEMORY_CHUNKS_UPDATED_AT),
    ("007_assistant_configs.sql", ASSISTANT_CONFIGS),
    ("008_source_at_scanned_at.sql", SOURCE_AT),
    ("009_favorites.sql", FAVORITES),
    ("010_memory_extracted_until.sql", MEMORY_EXTRACTED_UNTIL),
    ("011_memory_source_ui_pin.sql", MEMORY_SOURCE_UI_PIN),
];

/// `migration_name` matches the Python package's `applied_migrations` table
/// so a mixed-history database works without a rename migration.
fn ensure_ledger(client: &mut Client) -> Result<()> {
    client
        .batch_execute(
            "CREATE TABLE IF NOT EXISTS applied_migrations (
                migration_name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT now()
            )",
        )
        .context("create applied_migrations")?;
    Ok(())
}

pub fn migrate(client: &mut Client) -> Result<usize> {
    ensure_ledger(client)?;

    let mut applied = 0;
    for (name, sql) in MIGRATIONS {
        let already: i64 = client
            .query_one(
                "SELECT COUNT(*) FROM applied_migrations WHERE migration_name = $1",
                &[name],
            )
            .context("check applied_migrations")?
            .get(0);
        if already > 0 {
            continue;
        }
        let mut tx = client.transaction().context("begin migration")?;
        tx.batch_execute(sql)
            .with_context(|| format!("apply {name}"))?;
        tx.execute(
            "INSERT INTO applied_migrations (migration_name) VALUES ($1) ON CONFLICT DO NOTHING",
            &[name],
        )?;
        tx.commit().with_context(|| format!("commit {name}"))?;
        applied += 1;
    }
    Ok(applied)
}

pub fn status(client: &mut Client) -> Result<Vec<(String, bool)>> {
    ensure_ledger(client)?;
    let mut out = Vec::new();
    for (name, _) in MIGRATIONS {
        let already: i64 = client
            .query_one(
                "SELECT COUNT(*) FROM applied_migrations WHERE migration_name = $1",
                &[name],
            )?
            .get(0);
        out.push((name.to_string(), already > 0));
    }
    Ok(out)
}
