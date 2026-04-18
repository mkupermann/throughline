#!/bin/bash
# Claude Memory — DB Backup Script
# Erstellt täglich einen pg_dump der claude_memory DB und rotiert alte Backups.

set -e

# Default backup location is OUTSIDE the repo (XDG-style).
# Override with the CLAUDE_MEMORY_BACKUP_DIR environment variable.
BACKUP_DIR="${CLAUDE_MEMORY_BACKUP_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-memory/backups}"
RETENTION_DAYS=30
DB_NAME="${PGDATABASE:-claude_memory}"
DB_USER="${PGUSER:-$USER}"
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

echo "[$(date)] Backup startet → $BACKUP_FILE"

"$PG_BIN/pg_dump" -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date)] Backup fertig: $SIZE"

# Alte Backups löschen (> RETENTION_DAYS)
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete

echo "[$(date)] Retention: Backups > ${RETENTION_DAYS} Tage gelöscht"
echo "[$(date)] Verfügbare Backups:"
ls -lh "$BACKUP_DIR"/${DB_NAME}_*.sql.gz 2>/dev/null | tail -5
