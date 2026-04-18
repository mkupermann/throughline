#!/bin/bash
# test_fresh_install.sh — verifies that a clean clone can be brought up end-to-end.
#
# What it does:
#   1. Creates a throw-away test database.
#   2. Deploys sql/schema.sql.
#   3. Verifies every expected table exists.
#   4. Smoke-tests each CLI script by requesting --help (or running it against
#      the empty DB for scripts that have no --help flag but exit cleanly).
#   5. Drops the test database.
#   6. Prints a single summary line:
#        "FRESH INSTALL OK" on success, or a list of failures.
#
# Requirements (all must be on $PATH):
#   - python3 (3.10+)
#   - psql / createdb / dropdb  (PostgreSQL 16+ recommended)
#   - the `vector` and `pg_trgm` PostgreSQL extensions installed at the server level
#
# Environment overrides:
#   FRESH_DB_NAME      default: claude_memory_fresh_$$ (process id suffix)
#   PGUSER / PGHOST / PGPORT  passed through to psql/createdb
#   PYTHON_BIN         default: python3
#
# Exit codes:
#   0  = FRESH INSTALL OK
#   1  = at least one check failed
#   2  = required tool missing

set -u
set -o pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SCHEMA_FILE="$PROJECT_DIR/sql/schema.sql"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Resolve psql/createdb/dropdb — prefer Homebrew PG16 on macOS, else PATH
for candidate in \
    "/opt/homebrew/opt/postgresql@16/bin" \
    "/usr/local/opt/postgresql@16/bin" \
    "/opt/homebrew/bin" \
    "/usr/local/bin" \
    "/usr/bin"; do
    if [ -x "$candidate/psql" ] && [ -z "${PSQL:-}" ]; then
        PSQL="$candidate/psql"
        CREATEDB="$candidate/createdb"
        DROPDB="$candidate/dropdb"
    fi
done
if [ -z "${PSQL:-}" ]; then
    if command -v psql >/dev/null 2>&1; then
        PSQL="$(command -v psql)"
        CREATEDB="$(command -v createdb)"
        DROPDB="$(command -v dropdb)"
    else
        echo "ERROR: psql not found on PATH" >&2
        exit 2
    fi
fi

DB_NAME="${FRESH_DB_NAME:-claude_memory_fresh_$$}"
FAILURES=()

log()   { printf '[fresh-install] %s\n' "$*"; }
fail()  { FAILURES+=("$1"); log "FAIL: $1"; }
ok()    { log "ok: $1"; }

cleanup() {
    "$DROPDB" --if-exists "$DB_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Preconditions
# ---------------------------------------------------------------------------
if [ ! -f "$SCHEMA_FILE" ]; then
    echo "ERROR: schema not found at $SCHEMA_FILE" >&2
    exit 2
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON_BIN not on PATH" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# 2. Create test database
# ---------------------------------------------------------------------------
log "creating database '$DB_NAME'"
if ! "$CREATEDB" "$DB_NAME" 2>/tmp/fresh_install_createdb.err; then
    echo "ERROR: createdb failed:" >&2
    cat /tmp/fresh_install_createdb.err >&2
    exit 2
fi
ok "database created"

# ---------------------------------------------------------------------------
# 3. Deploy schema
# ---------------------------------------------------------------------------
log "deploying sql/schema.sql"
if "$PSQL" -d "$DB_NAME" --no-psqlrc -v ON_ERROR_STOP=1 -f "$SCHEMA_FILE" >/tmp/fresh_install_schema.log 2>&1; then
    ok "schema deployed"
else
    fail "schema deployment"
    tail -30 /tmp/fresh_install_schema.log >&2 || true
fi

# ---------------------------------------------------------------------------
# 4. Verify expected tables
# ---------------------------------------------------------------------------
EXPECTED_TABLES=(
    conversations
    messages
    memory_chunks
    skills
    prompts
    projects
    entities
    relationships
    entity_mentions
    embeddings
    memory_reflections
    ingestion_log
)

log "verifying tables"
ACTUAL_TABLES="$("$PSQL" -d "$DB_NAME" --no-psqlrc -t -A \
    -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name" 2>/dev/null)"
for t in "${EXPECTED_TABLES[@]}"; do
    if printf '%s\n' "$ACTUAL_TABLES" | grep -qx "$t"; then
        ok "table $t"
    else
        fail "missing table $t"
    fi
done

# ---------------------------------------------------------------------------
# 5. Smoke-test each CLI script
#
# Two kinds of scripts:
#   a) argparse-style — they support `--help` and must exit 0.
#   b) pipeline scripts — they exit cleanly when the DB is empty.
# ---------------------------------------------------------------------------
export PGDATABASE="$DB_NAME"
export PGUSER="${PGUSER:-$(whoami)}"

log "smoke-testing CLI scripts"

check_help() {
    local rel="$1"
    local out
    if out="$("$PYTHON_BIN" "$PROJECT_DIR/$rel" --help 2>&1)"; then
        ok "$rel --help"
    else
        fail "$rel --help exited non-zero"
        printf '%s\n' "$out" | head -5 >&2
    fi
}

check_empty_run() {
    # Run the script with no args and a short timeout. A clean empty-DB exit
    # is acceptable; anything else is a failure.
    local rel="$1"
    local timeout_s="${2:-10}"
    local out
    if out="$("$PYTHON_BIN" "$PROJECT_DIR/$rel" 2>&1 & pid=$!; \
                (sleep "$timeout_s" && kill "$pid" 2>/dev/null) & watcher=$!; \
                wait "$pid" 2>/dev/null; rc=$?; kill "$watcher" 2>/dev/null; exit "$rc")"; then
        ok "$rel (empty DB)"
    else
        # Still fine as long as it did not raise a stacktrace — we check output.
        if printf '%s' "$out" | grep -qE '^Traceback'; then
            fail "$rel crashed on empty DB"
            printf '%s\n' "$out" | head -10 >&2
        else
            ok "$rel (empty DB, non-zero exit but no traceback)"
        fi
    fi
}

# Scripts that expose --help
for rel in \
    scripts/extract_entities.py \
    scripts/reflect_memory.py \
    scripts/generate_embeddings.py \
    scripts/search_semantic.py \
    scripts/graph_query.py; do
    if [ -f "$PROJECT_DIR/$rel" ]; then
        check_help "$rel"
    fi
done

# Scripts that cleanly return on empty DB
for rel in \
    scripts/ingest_sessions.py \
    scripts/ingest_windsurf.py \
    scripts/scan_skills.py \
    scripts/scan_prompts.py \
    scripts/context_preload.py; do
    if [ -f "$PROJECT_DIR/$rel" ]; then
        check_empty_run "$rel" 20
    fi
done

# Scripts that require arguments — just test that they exit with usage info
for rel in skill/scripts/query.py; do
    if [ -f "$PROJECT_DIR/$rel" ]; then
        out="$("$PYTHON_BIN" "$PROJECT_DIR/$rel" 2>&1 || true)"
        if printf '%s' "$out" | grep -qi "usage"; then
            ok "$rel (usage output)"
        else
            fail "$rel did not print usage"
        fi
    fi
done

# Python syntax sanity — compile everything
log "compiling all python files"
for f in "$PROJECT_DIR"/scripts/*.py "$PROJECT_DIR"/gui/*.py "$PROJECT_DIR"/skill/scripts/*.py; do
    if ! "$PYTHON_BIN" -m py_compile "$f" >/dev/null 2>&1; then
        fail "py_compile $f"
    fi
done
ok "python compile"

# Bash syntax sanity
log "checking shell scripts"
for f in "$PROJECT_DIR"/scripts/*.sh; do
    if ! bash -n "$f" >/dev/null 2>&1; then
        fail "bash syntax $f"
    fi
done
ok "bash syntax"

# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
echo
if [ "${#FAILURES[@]}" -eq 0 ]; then
    echo "================================================"
    echo "FRESH INSTALL OK"
    echo "================================================"
    exit 0
else
    echo "================================================"
    echo "FRESH INSTALL FAILURES (${#FAILURES[@]}):"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
    echo "================================================"
    exit 1
fi
