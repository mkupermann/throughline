#!/usr/bin/env bash
# Loads the bundled demo data into a fresh claude_memory database.
#
# Usage:
#   ./scripts/load_demo.sh                 # local Postgres, current user
#   PGUSER=throughline PGHOST=localhost ./scripts/load_demo.sh
#   ./scripts/load_demo.sh --reset         # drop + re-create the DB, then load
#
# Requires: psql on PATH, a reachable Postgres, and the schema already applied.
# Safe to run against docker-compose's postgres service — set PGHOST=localhost
# and PGUSER=throughline first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PGDATABASE="${PGDATABASE:-claude_memory}"
PGUSER="${PGUSER:-${USER:-postgres}}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"

SCHEMA_SQL="$REPO_ROOT/sql/schema.sql"
DEMO_SQL="$REPO_ROOT/examples/demo_data.sql"

RESET=0
for arg in "$@"; do
    case "$arg" in
        --reset) RESET=1 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "$DEMO_SQL" ]]; then
    echo "ERROR: $DEMO_SQL not found." >&2
    exit 1
fi

export PGUSER PGHOST PGPORT

if [[ $RESET -eq 1 ]]; then
    echo "==> Dropping and re-creating database '$PGDATABASE' ..."
    psql -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ${PGDATABASE};"
    psql -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${PGDATABASE};"
fi

if [[ ! -f "$SCHEMA_SQL" ]]; then
    echo "ERROR: $SCHEMA_SQL not found." >&2
    exit 1
fi

# Only apply the schema if the core table is missing — keeps this idempotent.
HAS_CONVERSATIONS="$(psql -d "$PGDATABASE" -tAc \
    "SELECT to_regclass('public.conversations') IS NOT NULL")"
if [[ "$HAS_CONVERSATIONS" != "t" ]]; then
    echo "==> Applying schema from $SCHEMA_SQL ..."
    psql -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f "$SCHEMA_SQL"
else
    echo "==> Schema already present, skipping schema load."
fi

echo "==> Loading demo data from $DEMO_SQL ..."
psql -d "$PGDATABASE" -v ON_ERROR_STOP=1 -f "$DEMO_SQL"

echo
echo "Demo data loaded into '${PGDATABASE}' on ${PGHOST}:${PGPORT} as ${PGUSER}."
echo "Next steps:"
echo "  streamlit run gui/app.py"
echo "  # or, via docker-compose:"
echo "  docker compose up gui"
