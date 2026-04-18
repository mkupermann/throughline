---
name: claude-memory
description: Access the persistent memory database (PostgreSQL `claude_memory`) for long-term context across Claude Code sessions. Triggers on "what do I know about", "memory lookup", "recall", "memory db", "what did we decide about", "similar conversations", "lookup context", "/memory". Provides semantic search, SQL queries and the ability to add new insights.
---

# Claude Memory

Persistent long-term context for Claude Code sessions via a local PostgreSQL DB.

## DB connection

```
Host: localhost:5432
Database: claude_memory
User: $PGUSER (defaults to the current OS user; trust auth, no password)
```

Python (via `psycopg2`):
```python
import os, getpass, psycopg2
user = os.environ.get("PGUSER") or getpass.getuser()
conn = psycopg2.connect(host="localhost", port=5432, dbname="claude_memory", user=user)
```

SQL shell: `psql -d claude_memory` (use `/opt/homebrew/opt/postgresql@16/bin/psql` on macOS if `$PATH` is not set).

## Tables

| Table | Purpose | Columns |
|-------|---------|---------|
| `conversations` | Claude Code sessions | id, session_id, project_name, model, started_at, message_count, summary |
| `messages` | Individual messages | conversation_id, role, content, tool_name, created_at |
| `memory_chunks` | Extracted insights | content, category, tags, confidence, project_name |
| `skills` | Skill metadata | name, description, path, triggers, use_count |
| `projects` | Project context | name, description, contacts JSONB, decisions JSONB |
| `prompts` | Reusable templates | name, category, content, tags |

`memory_chunks.category` enum: `decision | pattern | insight | preference | contact | error_solution | project_context | workflow`

## Workflows

### 1. Recall ("what do I know about X?")

```bash
psql -d claude_memory -c "
SELECT category::text, content, confidence, project_name, created_at
FROM memory_chunks
WHERE content ILIKE '%SEARCH_TERM%'
   OR 'SEARCH_TERM' = ANY(tags)
   OR project_name ILIKE '%SEARCH_TERM%'
ORDER BY confidence DESC, created_at DESC
LIMIT 20;
"
```

Use `scripts/query.py` for structured queries:
```bash
python3 ~/.claude/skills/claude-memory/scripts/query.py search "pgvector tuning"
python3 ~/.claude/skills/claude-memory/scripts/query.py project "Project Alpha"
python3 ~/.claude/skills/claude-memory/scripts/query.py contact "Jane Doe"
python3 ~/.claude/skills/claude-memory/scripts/query.py decisions
python3 ~/.claude/skills/claude-memory/scripts/query.py stats
```

### 2. Add an insight

```bash
python3 ~/.claude/skills/claude-memory/scripts/add.py \
  --category decision \
  --content "We use pgvector instead of Milvus because of lower operational complexity" \
  --project claude-memory \
  --tags postgresql,pgvector,architecture \
  --confidence 0.9
```

Valid categories: `decision, pattern, insight, preference, contact, error_solution, project_context, workflow`

### 3. Find similar conversations

```sql
SELECT c.id, c.project_name, c.started_at, substring(m.content, 1, 200)
FROM conversations c
JOIN messages m ON m.conversation_id = c.id
WHERE m.content ILIKE '%SEARCH_TERM%'
ORDER BY c.started_at DESC
LIMIT 10;
```

### 4. Load project knowledge

```sql
SELECT category::text, content, tags, confidence
FROM memory_chunks
WHERE project_name = 'Project Alpha'
ORDER BY category, confidence DESC;
```

## Typical queries during Claude Code sessions

When the user asks:

- **"What do I know about topic X?"** -> `search` with ILIKE over content + tags
- **"Who is person Y?"** -> filter by category=`contact`
- **"What did we decide about Z?"** -> category=`decision` + ILIKE
- **"How did we solve problem A?"** -> category=`error_solution`
- **"Any patterns for B?"** -> category=`pattern`
- **"User preferences?"** -> category=`preference`

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/query.py` | Structured queries (search, project, contact, decisions, stats) |
| `scripts/add.py` | Add a new memory chunk |

## DB maintenance

Run the ingestion scripts from wherever you cloned the repo (adjust path as needed):

```bash
# Ingestion scripts (in the claude-memory repo)
python3 /path/to/claude-memory/scripts/ingest_sessions.py
python3 /path/to/claude-memory/scripts/scan_skills.py
python3 /path/to/claude-memory/scripts/extract_memory.py

# Open the GUI
cd /path/to/claude-memory/gui
streamlit run app.py  # -> http://localhost:8501
```

## Best practices

- **Set confidence deliberately**: 0.9+ for clear facts, 0.7-0.8 for interpretations, avoid anything under 0.5
- **Keep tags low-cardinality**: e.g. `postgresql`, `migration` rather than full sentences
- **Use the `project` field**: enables project-specific filters
- **Avoid duplicates**: `search` first before adding
- **Pick categories carefully**: one chunk = one category
