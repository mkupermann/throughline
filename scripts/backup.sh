#!/bin/bash
# Claude Memory — DB Backup Script
# Writes a daily pg_dump of the Throughline database and rotates old backups.

set -e
# `set -e` alone does NOT catch a failing pg_dump in `pg_dump | gzip`, because a
# pipeline's exit status is its LAST command and gzip happily succeeds on empty
# input. That is how a broken pgvector produced a 20-byte file that looked like
# a backup. pipefail makes the pipeline fail when pg_dump does.
set -o pipefail

# Default backup location is OUTSIDE the repo (XDG-style).
# Override with the CLAUDE_MEMORY_BACKUP_DIR environment variable.
BACKUP_DIR="${CLAUDE_MEMORY_BACKUP_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-memory/backups}"
RETENTION_DAYS=30
DB_NAME="${PGDATABASE:-throughline}"
DB_USER="${PGUSER:-$USER}"
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

echo "[$(date)] Backup startet → $BACKUP_FILE"

# Minimum plausible size. A real dump of this database is several MB; anything
# smaller means the dump aborted, whatever the exit status claimed.
MIN_BYTES="${CLAUDE_MEMORY_BACKUP_MIN_BYTES:-100000}"

fail() {
    echo "[$(date)] FEHLER: $1 — unvollständige Datei wird entfernt" >&2
    rm -f "$BACKUP_FILE"
    exit 1
}

if ! "$PG_BIN/pg_dump" -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_FILE"; then
    fail "pg_dump fehlgeschlagen"
fi

# Three independent checks, because a backup you cannot restore is not a backup.
BYTES=$(wc -c < "$BACKUP_FILE" | tr -d ' ')
[ "$BYTES" -ge "$MIN_BYTES" ] || fail "Backup ist nur ${BYTES} Bytes (< ${MIN_BYTES})"
gzip -t "$BACKUP_FILE" 2>/dev/null || fail "Backup ist kein gültiges gzip"

# Count COPY blocks with `grep -c`, never `grep -q`: under `pipefail`, grep -q
# exits at the first match, gzip then dies of SIGPIPE, and the pipeline reports
# failure for a perfectly good backup. That false negative would delete healthy
# dumps — strictly worse than the missing check it replaced.
TABLES=$(gzip -dc "$BACKUP_FILE" | grep -c '^COPY ' || true)
[ "${TABLES:-0}" -gt 0 ] || fail "Backup enthält keine Daten (kein COPY-Block)"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup fertig: $SIZE, $TABLES Tabellen, verifiziert"

# Alte Backups löschen (> RETENTION_DAYS) — nur wenn danach noch mindestens
# ein Backup übrig bleibt. Rotation darf niemals den letzten Stand löschen.
TOTAL=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f | wc -l | tr -d ' ')
if [ "$TOTAL" -gt 1 ]; then
    find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete
fi

echo "[$(date)] Retention: Backups > ${RETENTION_DAYS} Tage gelöscht"
echo "[$(date)] Verfügbare Backups:"
ls -lh "$BACKUP_DIR"/${DB_NAME}_*.sql.gz 2>/dev/null | tail -5
