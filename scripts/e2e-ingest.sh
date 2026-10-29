#!/bin/sh
set -e
echo "jsonl count:"
find /home/throughline/.claude/projects -name '*.jsonl' 2>/dev/null | wc -l
echo "sample:"
find /home/throughline/.claude/projects -name '*.jsonl' 2>/dev/null | head -5
echo "=== ingest verbose ==="
throughline ingest --source claude_code -v
echo "=== status ==="
throughline status
echo "=== search ==="
throughline search "PostgreSQL" --limit 3
echo "=== indexes ==="
# skip psql if missing
command -v psql >/dev/null && psql "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "select indexname from pg_indexes where tablename='messages' and indexname like '%uuid%'"
