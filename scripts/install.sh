#!/bin/bash
# Throughline — macOS setup script.
#
# Installs PostgreSQL 16 + pgvector via Homebrew, creates the database, applies
# the schema, installs Python dependencies, and loads the launchd jobs that keep
# ingestion current. Safe to re-run: every step checks before it acts.
#
# Linux users want docker compose (see docs/DEPLOYMENT.md) or the systemd units
# in systemd/ — this script assumes Homebrew and launchd.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PG_BIN="/opt/homebrew/opt/postgresql@16/bin"

# The database name the CLI defaults to. Override by exporting PGDATABASE
# before running, or by writing it into .env afterwards.
DB_NAME="${PGDATABASE:-throughline}"

echo "========================================="
echo "Throughline — installation"
echo "========================================="
echo ""

# 1. PostgreSQL 16
if ! command -v "$PG_BIN/psql" &> /dev/null; then
    echo "→ Installing PostgreSQL 16 via Homebrew..."
    brew install postgresql@16
    brew services start postgresql@16
    sleep 3
else
    echo "✓ PostgreSQL 16 present"
fi

# 2. pgvector — built against this PostgreSQL, not the Homebrew default
if ! ls "$PG_BIN/../share/postgresql@16/extension/vector.control" &> /dev/null; then
    echo "→ Installing pgvector (compiling against pg16)..."
    cd /tmp
    if [ ! -d pgvector ]; then
        git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
    fi
    cd pgvector
    make PG_CONFIG="$PG_BIN/pg_config"
    make install PG_CONFIG="$PG_BIN/pg_config"
    cd "$PROJECT_DIR"
else
    echo "✓ pgvector present"
fi

# 3. Database
if ! "$PG_BIN/psql" -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    echo "→ Creating database $DB_NAME..."
    "$PG_BIN/createdb" "$DB_NAME"
else
    echo "✓ Database $DB_NAME present"
fi

# 4. Schema
echo "→ Applying schema..."
"$PG_BIN/psql" -d "$DB_NAME" < "$PROJECT_DIR/sql/schema.sql" > /dev/null 2>&1 \
    || echo "  (schema already applied)"

# 5. Python dependencies
echo "→ Installing Python dependencies..."
pip3 install --break-system-packages -r "$PROJECT_DIR/requirements.txt" 2>&1 | tail -3
pip3 install --break-system-packages -e "$PROJECT_DIR" 2>&1 | tail -1

# 6. launchd jobs
echo "→ Configuring launchd jobs..."
LAUNCHD_DST="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCHD_DST"

for plist in "$PROJECT_DIR/launchd/"*.plist; do
    NAME=$(basename "$plist")
    sed \
        -e "s|REPLACE_WITH_ABSOLUTE_PATH|$PROJECT_DIR|g" \
        -e "s|REPLACE_WITH_YOUR_USER|$USER|g" \
        -e "s|REPLACE_WITH_HOME|$HOME|g" \
        -e "s|/Users/REPLACE|$HOME|g" \
        "$plist" > "$LAUNCHD_DST/$NAME"
    launchctl unload "$LAUNCHD_DST/$NAME" 2>/dev/null || true
    launchctl load "$LAUNCHD_DST/$NAME"
    echo "  ✓ $NAME"
done

chmod +x "$PROJECT_DIR/scripts/"*.sh

echo ""
echo "========================================="
echo "✓ Installation complete"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Ingest everything:  throughline ingest --all"
echo "  2. Index your skills:  throughline scan-skills"
echo "  3. Check the install:  throughline doctor"
echo "  4. Open the UI:        throughline serve"
echo ""
echo "The launchd jobs run on their own from here:"
echo "  - ingest:  hourly"
echo "  - extract: daily at 02:00"
echo "  - backup:  daily at 03:00"
