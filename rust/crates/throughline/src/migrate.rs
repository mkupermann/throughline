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

/// Databases created from the Python `sql/schema.sql` have the view
/// `v_conversation_stats` on these columns, and ALTER COLUMN TYPE refuses to
/// run under a view. Drop it and re-create it only where it existed.
const TOKEN_BIGINT: &str = r#"
DO $$
DECLARE
    had_view boolean := to_regclass('public.v_conversation_stats') IS NOT NULL;
BEGIN
    DROP VIEW IF EXISTS public.v_conversation_stats;
    ALTER TABLE conversations ALTER COLUMN token_count_in TYPE BIGINT;
    ALTER TABLE conversations ALTER COLUMN token_count_out TYPE BIGINT;
    IF had_view THEN
        CREATE VIEW public.v_conversation_stats AS
        SELECT project_name,
               count(*) AS sessions,
               sum(message_count) AS total_messages,
               round(avg(token_count_in + token_count_out)) AS avg_tokens,
               sum(cost_usd) AS total_cost
        FROM public.conversations
        GROUP BY project_name
        ORDER BY count(*) DESC;
    END IF;
END $$;
"#;

/// Valid time of memory notes: `valid_from` / `valid_to` (half-open interval).
/// The backfill lives in its own constant so a test can run it alone.
macro_rules! valid_time_columns {
    () => {
        r#"
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ;
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ;
ALTER TABLE memory_chunks ALTER COLUMN valid_from SET DEFAULT now();
"#
    };
}

/// Fills `valid_from` and closes the interval of superseded and merged notes.
/// UPDATEs only and repeatable: a second run changes nothing.
macro_rules! valid_time_backfill {
    () => {
        r#"
UPDATE memory_chunks SET valid_from = COALESCE(source_at, created_at, now()) WHERE valid_from IS NULL;
UPDATE memory_chunks o
   SET valid_to = GREATEST(o.valid_from, COALESCE(
         (SELECT n.valid_from FROM memory_chunks n WHERE n.id = o.superseded_by),
         o.superseded_at, o.valid_from))
 WHERE o.status IN ('superseded', 'merged') AND o.valid_to IS NULL;
"#
    };
}
pub const VALID_TIME_BACKFILL: &str = valid_time_backfill!();

/// Indexes reuse the names of older databases so nothing is created twice.
macro_rules! valid_time_indexes {
    () => {
        r#"
CREATE INDEX IF NOT EXISTS idx_chunks_status ON memory_chunks (status);
CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_chunks (project_name);
CREATE INDEX IF NOT EXISTS idx_memory_valid_from ON memory_chunks (valid_from);
"#
    };
}

/// Migration 012: columns, then the backfill, then the indexes.
const MEMORY_VALID_TIME: &str = concat!(
    valid_time_columns!(),
    valid_time_backfill!(),
    valid_time_indexes!()
);

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

/// Recall looks up successors (`superseded_by = id`) per candidate and per
/// associative step. Only replaced notes carry the column, so the index is
/// partial.
const MEMORY_SUPERSEDED_BY_INDEX: &str = r#"
CREATE INDEX IF NOT EXISTS idx_memory_superseded_by ON memory_chunks (superseded_by) WHERE superseded_by IS NOT NULL;
"#;

/// Team mode. Accounts, sessions and login attempts as in the Python-era
/// schema, but sessions without `last_seen_at`, plus per-account agent keys.
/// The Python audit trail goes: `access_audit`, its append-only trigger, and
/// every writer. CASCADE on a trigger function drops each trigger that runs
/// it (`access_changes` sits on nine tables). Function bodies are not
/// dependencies, so `record_conversation_project` is replaced by the same
/// body without its audit insert. Notes get an owner (NULL for existing
/// notes) and a visibility (existing notes are shared); favorites become per
/// account (NULL in local mode). Idempotent, so the Python-era schema and a
/// fresh Rust schema end up the same.
const TEAM_MODE: &str = r#"
CREATE TABLE IF NOT EXISTS access_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text UNIQUE NOT NULL CHECK (username ~ '^[a-z0-9][a-z0-9._@-]{0,119}$'),
    display_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS access_sessions (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES access_users(id) ON DELETE CASCADE,
    csrf_token text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
ALTER TABLE access_sessions DROP COLUMN IF EXISTS last_seen_at;
CREATE INDEX IF NOT EXISTS access_sessions_user ON access_sessions (user_id);
CREATE TABLE IF NOT EXISTS access_attempts (
    key text PRIMARY KEY,
    attempts integer NOT NULL,
    window_start timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS access_agent_keys (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES access_users(id) ON DELETE CASCADE,
    key_hash text NOT NULL UNIQUE,
    label text,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS access_agent_keys_user ON access_agent_keys (user_id);

DROP FUNCTION IF EXISTS public.record_access_change() CASCADE;
DROP FUNCTION IF EXISTS public.audit_processing_lifecycle() CASCADE;
DO $$
BEGIN
    IF to_regprocedure('public.record_conversation_project()') IS NOT NULL THEN
        CREATE OR REPLACE FUNCTION public.record_conversation_project() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
        BEGIN
            IF OLD.assigned_project IS DISTINCT FROM NEW.assigned_project THEN
                INSERT INTO public.conversation_project_changes(conversation_id,previous_project,assigned_project,source_path,actor)
                    VALUES(NEW.id,OLD.assigned_project,NEW.assigned_project,NEW.project_path,
                        COALESCE(NULLIF(current_setting('throughline.actor',true),''),'operator'));
            END IF;
            IF OLD.project_name IS DISTINCT FROM NEW.project_name THEN
                UPDATE public.memory_chunks SET project_name=NEW.project_name
                    WHERE source_type='conversation' AND source_id=NEW.id;
            END IF;
            RETURN NEW;
        END $fn$;
    END IF;
END $$;
DROP TABLE IF EXISTS access_audit;
DROP FUNCTION IF EXISTS public.protect_access_audit();

ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS owner_id bigint REFERENCES access_users(id);
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'shared'
    CONSTRAINT memory_chunks_visibility_check CHECK (visibility IN ('shared', 'private'));
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'memory_chunks_private_has_owner'
                   AND conrelid = 'memory_chunks'::regclass) THEN
        ALTER TABLE memory_chunks ADD CONSTRAINT memory_chunks_private_has_owner
            CHECK (visibility = 'shared' OR owner_id IS NOT NULL);
    END IF;
END $$;

ALTER TABLE favorites ADD COLUMN IF NOT EXISTS user_id bigint REFERENCES access_users(id);
CREATE UNIQUE INDEX IF NOT EXISTS favorites_kind_ref_user_key
    ON favorites (kind, ref, (COALESCE(user_id, 0)));
ALTER TABLE favorites DROP CONSTRAINT IF EXISTS favorites_kind_ref_key;
"#;

/// Team mode keeps no activity time per account: an account's star has no
/// `created_at`, and stars made before this migration lose theirs. Local
/// stars (`user_id` NULL) keep their time and the column its default.
/// Idempotent.
const ACCOUNT_STARS_UNTIMED: &str = r#"
ALTER TABLE favorites ALTER COLUMN created_at DROP NOT NULL;
UPDATE favorites SET created_at = NULL WHERE user_id IS NOT NULL AND created_at IS NOT NULL;
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
    ("012_memory_valid_time.sql", MEMORY_VALID_TIME),
    (
        "013_memory_superseded_by_index.sql",
        MEMORY_SUPERSEDED_BY_INDEX,
    ),
    ("014_team_mode.sql", TEAM_MODE),
    ("015_account_stars_untimed.sql", ACCOUNT_STARS_UNTIMED),
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

#[cfg(test)]
mod tests {
    use super::*;

    fn test_db() -> Option<Client> {
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

    #[test]
    fn valid_time_backfill_closes_chains() {
        let Some(mut client) = test_db() else {
            eprintln!("skipping valid_time_backfill_closes_chains: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        let category_type = crate::extract_memory::category_column_type(&mut tx).expect("type");
        let tag = uuid::Uuid::new_v4().to_string();
        let ts = |s: &str| -> chrono::DateTime<chrono::Utc> {
            chrono::DateTime::parse_from_rfc3339(s).unwrap().into()
        };
        // Insert a row with valid_from = NULL so the backfill has work to do.
        let mut insert = |name: &str,
                          status: &str,
                          source_at: &str,
                          superseded_by: Option<i64>,
                          superseded_at: Option<&str>,
                          valid_from: Option<&str>|
         -> i64 {
            tx.query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category, confidence, \
                         status, source_at, superseded_by, superseded_at, valid_from) \
                     VALUES ('manual', $1, 'insight'::text::{category_type}, 0.5::float8, $2, $3, \
                         $4, $5, $6) RETURNING id"
                ),
                &[
                    &format!("{name} {tag}"),
                    &status,
                    &ts(source_at),
                    &superseded_by,
                    &superseded_at.map(ts),
                    &valid_from.map(ts),
                ],
            )
            .expect("insert chunk")
            .get(0)
        };
        let d = insert("D", "active", "2026-01-01T00:00:00Z", None, None, None);
        let b = insert(
            "B",
            "active",
            "2026-03-01T00:00:00Z",
            None,
            None,
            Some("2026-03-01T00:00:00Z"),
        );
        let a = insert(
            "A",
            "superseded",
            "2026-01-01T00:00:00Z",
            Some(b),
            None,
            None,
        );
        let c = insert("C", "merged", "2026-02-01T00:00:00Z", Some(d), None, None);
        let e = insert(
            "E",
            "superseded",
            "2026-02-01T00:00:00Z",
            None,
            Some("2026-05-01T00:00:00Z"),
            None,
        );

        // Compare as instants, independent of the session time zone.
        let instant = |tx: &mut postgres::Transaction, id: i64| -> (i64, Option<i64>) {
            let row = tx
                .query_one(
                    "SELECT extract(epoch FROM valid_from)::int8, \
                            extract(epoch FROM valid_to)::int8 FROM memory_chunks WHERE id = $1",
                    &[&id],
                )
                .expect("read chunk");
            (row.get(0), row.get(1))
        };

        tx.batch_execute(VALID_TIME_BACKFILL).expect("backfill");
        let first: Vec<_> = [a, b, c, d, e]
            .iter()
            .map(|i| instant(&mut tx, *i))
            .collect();
        let at = |s: &str| ts(s).timestamp();
        assert_eq!(
            first[0],
            (at("2026-01-01T00:00:00Z"), Some(at("2026-03-01T00:00:00Z")))
        );
        assert_eq!(first[1], (at("2026-03-01T00:00:00Z"), None));
        // merged into an older note: empty interval
        assert_eq!(
            first[2],
            (at("2026-02-01T00:00:00Z"), Some(at("2026-02-01T00:00:00Z")))
        );
        assert_eq!(first[3], (at("2026-01-01T00:00:00Z"), None));
        assert_eq!(
            first[4],
            (at("2026-02-01T00:00:00Z"), Some(at("2026-05-01T00:00:00Z")))
        );

        // Second run changes nothing.
        tx.batch_execute(VALID_TIME_BACKFILL)
            .expect("second backfill");
        let second: Vec<_> = [a, b, c, d, e]
            .iter()
            .map(|i| instant(&mut tx, *i))
            .collect();
        assert_eq!(first, second);
    }

    fn migration(name: &str) -> &'static str {
        MIGRATIONS
            .iter()
            .find(|(n, _)| *n == name)
            .unwrap_or_else(|| panic!("{name} is registered"))
            .1
    }

    #[test]
    fn superseded_by_index_migration_runs_twice() {
        let sql = migration("013_memory_superseded_by_index.sql");
        let Some(mut client) = test_db() else {
            eprintln!("skipping superseded_by_index_migration_runs_twice: no PostgreSQL reachable");
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        // twice: a second run is a no-op
        tx.batch_execute(sql).expect("first run");
        tx.batch_execute(sql).expect("second run");
        let def: String = tx
            .query_one(
                "SELECT indexdef FROM pg_indexes \
                 WHERE tablename = 'memory_chunks' AND indexname = 'idx_memory_superseded_by'",
                &[],
            )
            .expect("index exists")
            .get(0);
        assert!(def.contains("(superseded_by)"), "{def}");
        assert!(def.contains("WHERE (superseded_by IS NOT NULL)"), "{def}");
    }

    /// Copies of `tables` in a schema of their own, first on the search
    /// path of `tx`. A migration test changes these copies, never the shared
    /// tables, so it takes no lock that other tests running at the same time
    /// wait for (a column drop on the shared `memory_chunks` and `favorites`
    /// could deadlock with a test that reads and writes both). Rolled back
    /// with `tx`.
    fn own_schema(tx: &mut postgres::Transaction, tables: &[&str]) {
        let schema = format!("test_migration_{}", uuid::Uuid::new_v4().simple());
        let mut sql = format!("CREATE SCHEMA {schema}; SET LOCAL search_path TO {schema}, public;");
        for table in tables {
            sql.push_str(&format!(
                "CREATE TABLE {schema}.{table} (LIKE public.{table} INCLUDING ALL);"
            ));
        }
        tx.batch_execute(&sql).expect("schema of the test");
    }

    /// The state before 014 on any database: the Python-era access block of
    /// `sql/schema.sql` (sessions with `last_seen_at`, the audit table with
    /// its append-only trigger, `access_changes` on two tables, the two other
    /// functions that write the audit) and notes and favorites without the
    /// 014 columns. Run after [`own_schema`]: the account tables and the
    /// changed notes and favorites are the test's own. The audit table is in
    /// `public`, where the Python-era functions write it.
    const BEFORE_TEAM_MODE: &str = r#"
CREATE TABLE IF NOT EXISTS access_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text UNIQUE NOT NULL,
    display_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS access_sessions (
    token_hash text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES access_users(id) ON DELETE CASCADE,
    csrf_token text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
ALTER TABLE access_sessions ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL DEFAULT now();
CREATE TABLE IF NOT EXISTS public.access_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor text NOT NULL,
    action text NOT NULL,
    entity_type text NOT NULL
);
CREATE OR REPLACE FUNCTION public.record_access_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.access_audit(actor, action, entity_type) VALUES ('operator', TG_OP, TG_TABLE_NAME);
    RETURN COALESCE(NEW, OLD);
END $$;
DROP TRIGGER IF EXISTS access_changes ON access_users;
CREATE TRIGGER access_changes AFTER INSERT OR UPDATE OR DELETE ON access_users
    FOR EACH ROW EXECUTE FUNCTION public.record_access_change();
DROP TRIGGER IF EXISTS access_changes ON access_sessions;
CREATE TRIGGER access_changes AFTER INSERT OR UPDATE OR DELETE ON access_sessions
    FOR EACH ROW EXECUTE FUNCTION public.record_access_change();
CREATE OR REPLACE FUNCTION public.protect_access_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit records are append-only'; END $$;
DROP TRIGGER IF EXISTS access_audit_immutable ON public.access_audit;
CREATE TRIGGER access_audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.access_audit
    FOR EACH STATEMENT EXECUTE FUNCTION public.protect_access_audit();
CREATE OR REPLACE FUNCTION public.audit_processing_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.access_audit(actor, action, entity_type)
        VALUES ('operator', 'PROCESSING_' || upper(NEW.state), 'processing_runs');
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION public.record_conversation_project() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.access_audit(actor, action, entity_type) VALUES ('operator', 'ASSIGN', 'conversation');
    RETURN NEW;
END $$;
DO $$ BEGIN
    IF to_regclass('public.processing_runs') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS processing_lifecycle_audit ON public.processing_runs;
        CREATE TRIGGER processing_lifecycle_audit AFTER INSERT OR UPDATE ON public.processing_runs
            FOR EACH ROW EXECUTE FUNCTION public.audit_processing_lifecycle();
    END IF;
END $$;
ALTER TABLE memory_chunks DROP COLUMN IF EXISTS owner_id, DROP COLUMN IF EXISTS visibility;
ALTER TABLE favorites DROP COLUMN IF EXISTS user_id;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'favorites_kind_ref_key') THEN
        ALTER TABLE favorites ADD CONSTRAINT favorites_kind_ref_key UNIQUE (kind, ref);
    END IF;
END $$;
"#;

    #[test]
    fn migration_014_creates_access_tables_and_drops_audit() {
        let sql = migration("014_team_mode.sql");
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping migration_014_creates_access_tables_and_drops_audit: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        own_schema(&mut tx, &["memory_chunks", "favorites"]);
        tx.batch_execute(BEFORE_TEAM_MODE)
            .expect("state before 014");
        let category_type = crate::extract_memory::category_column_type(&mut tx).expect("type");
        let note: i64 = tx
            .query_one(
                &format!(
                    "INSERT INTO memory_chunks (source_type, content, category) \
                     VALUES ('manual', 'note before team mode', 'insight'::text::{category_type}) \
                     RETURNING id"
                ),
                &[],
            )
            .expect("insert note")
            .get(0);
        let user: i64 = tx
            .query_one(
                "INSERT INTO access_users (username, display_name, password_hash, role) \
                 VALUES ($1, 'Kept', 'not-a-hash', 'admin') RETURNING id",
                &[&format!("kept-{}", uuid::Uuid::new_v4())],
            )
            .expect("insert account")
            .get(0);
        tx.execute(
            "INSERT INTO access_sessions (token_hash, user_id, csrf_token, expires_at) \
             VALUES ($1, $2, 'request', now() + interval '1 day')",
            &[&uuid::Uuid::new_v4().to_string(), &user],
        )
        .expect("insert session");
        let one = |tx: &mut postgres::Transaction, q: &str| -> i64 {
            tx.query_one(q, &[]).expect(q).get(0)
        };
        assert!(one(&mut tx, "SELECT count(*) FROM access_audit") >= 2);
        let legacy = one(
            &mut tx,
            "SELECT count(*) FROM pg_class WHERE relname = 'processing_runs'",
        ) == 1;
        let run = |tx: &mut postgres::Transaction| {
            tx.execute(
                "INSERT INTO processing_runs (id, name) VALUES ($1, $1)",
                &[&format!("team-mode-{}", uuid::Uuid::new_v4())],
            )
        };
        let note_ref = note.to_string();
        tx.execute(
            "INSERT INTO favorites (kind, ref) VALUES ('memory', $1)",
            &[&note_ref],
        )
        .expect("favorite before 014");
        // A same-named constraint on another table must not stop 014 from
        // adding the CHECK to public.memory_chunks.
        tx.batch_execute(
            "CREATE TEMP TABLE team_mode_decoy (v text \
             CONSTRAINT memory_chunks_private_has_owner CHECK (v <> 'x'))",
        )
        .expect("decoy constraint");

        // twice: a second run is a no-op
        tx.batch_execute(sql).expect("first run");
        tx.batch_execute(sql).expect("second run");
        if legacy {
            run(&mut tx).expect("processing run after 014");
        }

        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM pg_class WHERE relname = 'access_audit'"
            ),
            0
        );
        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'access_changes'"
            ),
            0
        );
        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM pg_proc \
                 WHERE proname IN ('record_access_change', 'protect_access_audit', \
                                   'audit_processing_lifecycle')"
            ),
            0
        );
        // No function body writes the audit any more.
        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM pg_proc WHERE prosrc ILIKE '%access_audit%'"
            ),
            0
        );
        let project_history: String = tx
            .query_one(
                "SELECT prosrc FROM pg_proc WHERE proname = 'record_conversation_project'",
                &[],
            )
            .expect("record_conversation_project kept")
            .get(0);
        assert!(project_history.contains("INSERT INTO public.conversation_project_changes"));
        assert!(project_history.contains("UPDATE public.memory_chunks SET project_name"));
        for table in [
            "access_users",
            "access_sessions",
            "access_attempts",
            "access_agent_keys",
        ] {
            let found: Option<String> = tx
                .query_one("SELECT to_regclass($1)::text", &[&table])
                .expect("regclass")
                .get(0);
            assert_eq!(found.as_deref(), Some(table));
        }
        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM information_schema.columns \
                 WHERE table_name = 'access_sessions' AND column_name = 'last_seen_at'"
            ),
            0
        );
        // Accounts and sessions stay; existing notes are shared, without owner.
        assert_eq!(
            one(
                &mut tx,
                &format!("SELECT count(*) FROM access_sessions WHERE user_id = {user}")
            ),
            1
        );
        let row = tx
            .query_one(
                "SELECT visibility, owner_id FROM memory_chunks WHERE id = $1",
                &[&note],
            )
            .expect("note");
        assert_eq!(row.get::<_, String>(0), "shared");
        assert_eq!(row.get::<_, Option<i64>>(1), None);
        let mut savepoint = tx.transaction().expect("savepoint");
        assert!(savepoint
            .execute(
                "UPDATE memory_chunks SET visibility = 'secret' WHERE id = $1",
                &[&note],
            )
            .is_err());
        drop(savepoint);
        // A private note needs an owner.
        let mut savepoint = tx.transaction().expect("savepoint");
        assert!(savepoint
            .execute(
                "UPDATE memory_chunks SET visibility = 'private' WHERE id = $1",
                &[&note],
            )
            .is_err());
        drop(savepoint);
        tx.execute(
            "UPDATE memory_chunks SET visibility = 'private', owner_id = $2 WHERE id = $1",
            &[&note, &user],
        )
        .expect("private note with owner");

        // One favorite per item and account; local mode is the NULL account.
        crate::favorites::add(
            &mut tx,
            &crate::access::Actor::local(),
            crate::favorites::FavKind::Memory,
            &note_ref,
        )
        .expect("favorite again");
        let per_user = "INSERT INTO favorites (kind, ref, user_id) VALUES ('memory', $1, $2) \
                        ON CONFLICT (kind, ref, (COALESCE(user_id, 0))) DO NOTHING";
        assert_eq!(tx.execute(per_user, &[&note_ref, &user]).unwrap(), 1);
        assert_eq!(tx.execute(per_user, &[&note_ref, &user]).unwrap(), 0);
        assert_eq!(
            one(
                &mut tx,
                &format!("SELECT count(*) FROM favorites WHERE kind = 'memory' AND ref = '{note}'")
            ),
            2
        );
        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM pg_constraint WHERE conname = 'favorites_kind_ref_key'"
            ),
            0
        );
        // Accounts are disabled, never deleted: no cascade from them.
        assert_eq!(
            one(
                &mut tx,
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' \
                 AND confrelid = 'access_users'::regclass AND confdeltype = 'c' \
                 AND conrelid IN ('favorites'::regclass, 'memory_chunks'::regclass)"
            ),
            0
        );
    }

    #[test]
    fn migration_015_takes_the_time_off_account_stars() {
        use crate::access::{Actor, Role};
        use crate::favorites::FavKind;
        let sql = migration("015_account_stars_untimed.sql");
        let Some(mut client) = test_db() else {
            eprintln!(
                "skipping migration_015_takes_the_time_off_account_stars: no PostgreSQL reachable"
            );
            return;
        };
        let mut tx = client.transaction().expect("begin test tx");
        own_schema(&mut tx, &["favorites"]);
        // After 014: every star has a time.
        tx.batch_execute("ALTER TABLE favorites ALTER COLUMN created_at SET NOT NULL")
            .expect("state before 015");
        let star =
            "INSERT INTO favorites (kind, ref, user_id) VALUES ('project', $1, $2) RETURNING id";
        let local: i64 = tx
            .query_one(star, &[&"tl-local-star", &None::<i64>])
            .expect("local star")
            .get(0);
        let account: i64 = tx
            .query_one(star, &[&"tl-account-star", &Some(7_i64)])
            .expect("account star")
            .get(0);

        // twice: a second run is a no-op
        tx.batch_execute(sql).expect("first run");
        tx.batch_execute(sql).expect("second run");

        let time =
            |tx: &mut postgres::Transaction, r: &str| -> Option<chrono::DateTime<chrono::Utc>> {
                tx.query_one("SELECT created_at FROM favorites WHERE ref = $1", &[&r])
                    .expect(r)
                    .get(0)
            };
        assert!(
            time(&mut tx, "tl-local-star").is_some(),
            "a local star keeps its time"
        );
        assert_eq!(
            time(&mut tx, "tl-account-star"),
            None,
            "an account's star loses it"
        );
        assert!(local < account);
        // New stars: local mode with a time, an account without one.
        let user = Actor::for_user(7, "seven", Role::Editor);
        crate::favorites::add(&mut tx, &Actor::local(), FavKind::Project, "tl-local-new")
            .expect("local star");
        for r in ["tl-account-new", "tl-account-newer"] {
            crate::favorites::add(&mut tx, &user, FavKind::Project, r).expect("account star");
        }
        assert!(time(&mut tx, "tl-local-new").is_some());
        assert_eq!(time(&mut tx, "tl-account-new"), None);
        // Without a time the account's stars come newest first by id, and
        // their JSON has no time at all.
        let listed = crate::favorites::list(&mut tx, &user, None).expect("list");
        let refs: Vec<&str> = listed.iter().map(|f| f.ref_.as_str()).collect();
        assert_eq!(
            refs,
            ["tl-account-newer", "tl-account-new", "tl-account-star"]
        );
        let json = serde_json::to_value(&listed[0]).expect("json");
        assert_eq!(
            json,
            serde_json::json!({ "kind": "project", "ref": "tl-account-newer" })
        );
        let local = crate::favorites::list(&mut tx, &Actor::local(), None).expect("list");
        assert!(local.iter().all(|f| f.created_at.is_some()), "{local:?}");
    }
}
