#!/bin/bash
# Claude Memory — Setup Script
# Installiert PostgreSQL + pgvector, erstellt DB, deployed Schema, installiert launchd-Jobs.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PG_BIN="/opt/homebrew/opt/postgresql@16/bin"

echo "========================================="
echo "Claude Memory — Installation"
echo "========================================="
echo ""

# 1. PostgreSQL 16 installieren
if ! command -v "$PG_BIN/psql" &> /dev/null; then
    echo "→ Installiere PostgreSQL 16 via Homebrew..."
    brew install postgresql@16
    brew services start postgresql@16
    sleep 3
else
    echo "✓ PostgreSQL 16 vorhanden"
fi

# 2. pgvector installieren (selbst kompilieren für pg16)
if ! ls "$PG_BIN/../share/postgresql@16/extension/vector.control" &> /dev/null; then
    echo "→ Installiere pgvector (kompilieren für pg16)..."
    cd /tmp
    if [ ! -d pgvector ]; then
        git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
    fi
    cd pgvector
    make PG_CONFIG="$PG_BIN/pg_config"
    make install PG_CONFIG="$PG_BIN/pg_config"
    cd "$PROJECT_DIR"
else
    echo "✓ pgvector vorhanden"
fi

# 3. Datenbank erstellen
if ! "$PG_BIN/psql" -lqt | cut -d \| -f 1 | grep -qw claude_memory; then
    echo "→ Erstelle Datenbank claude_memory..."
    "$PG_BIN/createdb" claude_memory
else
    echo "✓ Datenbank claude_memory vorhanden"
fi

# 4. Schema deployen
echo "→ Deploye Schema..."
"$PG_BIN/psql" -d claude_memory < "$PROJECT_DIR/sql/schema.sql" > /dev/null 2>&1 || echo "  (Schema bereits deployed)"

# 5. Python-Dependencies
echo "→ Installiere Python-Dependencies..."
pip3 install --break-system-packages -r "$PROJECT_DIR/requirements.txt" 2>&1 | tail -3

# 6. launchd-Plists konfigurieren (Platzhalter ersetzen)
echo "→ Konfiguriere launchd-Plists..."
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
echo "✓ Installation abgeschlossen"
echo "========================================="
echo ""
echo "Nächste Schritte:"
echo "  1. Erste Ingestion:    python3 $PROJECT_DIR/scripts/ingest_sessions.py"
echo "  2. Skills scannen:     python3 $PROJECT_DIR/scripts/scan_skills.py"
echo "  3. GUI starten:        cd $PROJECT_DIR/gui && streamlit run app.py"
echo "  4. Memory extrahieren: python3 $PROJECT_DIR/scripts/extract_memory.py"
echo ""
echo "launchd-Jobs laufen automatisch:"
echo "  - Ingest:  stündlich"
echo "  - Extract: täglich 02:00"
echo "  - Backup:  täglich 03:00"
