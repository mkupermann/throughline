# Usage Guide

How to get value out of the tool after installation. Organized by workflow.

## Daily workflow

### 1. Let the scheduler do its job

If you installed the launchd jobs, everything happens automatically:

- **Every hour** — new Claude Code sessions are ingested into the database
- **Daily 02:00** — memory chunks are extracted from recent conversations
- **Daily 03:00** — a compressed `pg_dump` is written to
  `~/.local/share/claude-memory/backups/` with 30-day retention

Check status:

```bash
launchctl list | grep claude-memory
```

### 2. Start a new Claude session

When the `SessionStart` hook is installed, opening any Claude Code session
automatically writes `./.claude/MEMORY_CONTEXT.md` into the current
project — a curated summary of past decisions, patterns, and contacts
relevant to this directory.

Claude sees it on session start and has instant context.

### 3. Open the GUI when you need to review

```bash
cd gui && streamlit run app.py
```

Landing page: a 4-metric dashboard (conversations, messages, skills,
memory chunks), the 5 most recent sessions, and a category breakdown
of your memory.

## The 14 GUI pages, one paragraph each

### Dashboard

Shows the four core counts and a two-week activity chart. Use it as a
quick pulse check — sudden drops in ingestion usually mean the scheduler
is not running.

### Calendar

Month / Week / Day / List views of everything with a timestamp:
conversations, memory chunks, skill updates, projects, prompts,
entities, reflections, ingestion events. Each category is a checkbox
you can toggle. Click any event to open its detail page.

### Search

Full-text search across all tables. Pick which entity types to include
(conversations, messages, memory, skills, projects, prompts). Results
are grouped per type in collapsible sections. Click a row to open the
detail view.

### Semantic

Vector similarity search. Requires embeddings to be generated. Type a
query, get the N most semantically similar memory chunks plus messages
ranked by cosine distance.

### Conversations

Filter by project, model, full-text. Click a row to open a detail view
with the full chat history (newest first by default; toggle for
oldest-first).

### Memory

The memory chunks themselves. Filter by category, project, or text.
Create, edit, delete chunks manually. Each card shows category badge,
confidence indicator, tags, and creation date.

### Memory Health

Operational view: active / superseded / merged / stale counts, top
accessed chunks, recent reflection log (what the reflection engine
merged, contradicted, marked stale), and a button to launch a fresh
reflection run.

### Skills

Card grid of all discovered skills with use count and last-used date.
Click a skill to see its full `SKILL.md`.

### Knowledge Graph

Visual graph of entities (people, projects, technologies, decisions,
concepts, organizations) and their relationships. Sliders to adjust
node spacing, label size, and graph height. Click a node to open its
entity detail — full attribute list, incoming/outgoing relationships,
mention timeline.

### Projects

CRUD for the projects table. Each project has a status (active,
paused, completed, archived), free-form description, and JSONB fields
for contacts and decisions.

### Prompts

Your prompt library. Sorted newest-first by default; dropdown for
other orderings. Filter by category, search by text. Each card shows
a preview, tags, use count, and creation date.

### Scheduler

Status of the three launchd jobs — active / paused / loaded? Recent
log output. Buttons to run-now, pause, enable, or reinstall. Inline
view of the task prompt and tail of recent logs.

### Ingestion

Manual triggers for the four ingestion scripts: sessions, skills,
prompts, memory extraction. Shows counters for how many records are
in the database vs. how many conversations are still unprocessed.
Also houses a "generate titles" button and the ingestion log table.

### SQL

A raw SQL console for anything the GUI does not cover. Run SELECTs,
DML, whatever. A few snippet cards for common queries so you do not
have to remember the schema.

## Typical workflows

### I am starting a new project

1. Create a row in `projects` (GUI: Projects → New project, or manual
   SQL insert).
2. Start using Claude in the project directory. The session is
   automatically ingested within an hour.
3. The first time you revisit the project, the context pre-loader
   gives Claude the relevant past decisions and contacts.

### I need to find a past decision

Three routes, pick whichever feels fastest:

- **GUI → Search** — type a keyword, check only `memory_chunks`
- **GUI → Memory** — filter by category `decision`
- **CLI** — `python3 skill/scripts/query.py decisions`

### I want to see everyone I have talked about

- **GUI → Knowledge Graph**, filter entity type `person`

### I want to export my memory to Markdown

There is no built-in export yet. A one-liner that works today:

```bash
psql -d claude_memory -t -A -F"|" -c "
  SELECT category, content, confidence, project_name, tags
  FROM memory_chunks WHERE status='active'
  ORDER BY created_at DESC
" > memory_export.txt
```

For structured Markdown you can pipe through a small formatter or ask
Claude in a session:

```
Read memory_export.txt and write docs/memory.md grouped by category.
```

### Something looks wrong — I want to clean up

The memory reflection engine handles most cases automatically. If you
want to run it manually:

```bash
python3 scripts/reflect_memory.py                    # all four modes
python3 scripts/reflect_memory.py --mode dedup       # just dedup
python3 scripts/reflect_memory.py --mode contradictions
python3 scripts/reflect_memory.py --mode stale
python3 scripts/reflect_memory.py --mode consolidate
```

Deleting a chunk entirely: **GUI → Memory → open chunk → Delete**. The
row is hard-deleted — use the reflection log if you need to recover the
action.

## Using the Claude Code skill

Once `~/.claude/skills/claude-memory/` is in place, any Claude session
can call it. Typical prompts:

- *"What did we decide about pgvector?"*
- *"Find past sessions that mention Postgres migrations."*
- *"Remember that we chose Ollama over OpenAI for embeddings."*

The skill includes a query CLI that works standalone:

```bash
python3 ~/.claude/skills/claude-memory/scripts/query.py search "pgvector"
python3 ~/.claude/skills/claude-memory/scripts/query.py project "Project Alpha"
python3 ~/.claude/skills/claude-memory/scripts/query.py decisions
python3 ~/.claude/skills/claude-memory/scripts/query.py stats
```

## Performance notes

- **First ingestion of a large `~/.claude/projects/`** (100+ sessions)
  takes 30–90 seconds. Subsequent runs are fast — only new JSONL files
  are parsed.
- **Memory extraction** via Claude CLI runs at roughly one conversation
  per 15–30 seconds. For 100 unprocessed sessions: ~40 minutes.
- **Embeddings with Ollama** on Apple Silicon: about 20 chunks/second.
  OpenAI `text-embedding-3-small` at batch size 100: about 200 chunks
  per API call in under a second.
- **Streamlit on first load** can take 5–8 seconds to warm up. Leave the
  tab open.

## Tips

- **Pin a project** — set `CLAUDE_PROJECT_DIR=/path/to/project` in your
  shell when working on a specific codebase. The context pre-loader
  will scope its suggestions accordingly.
- **Use tags liberally** — memory chunks are cheap to tag, and tag
  filtering in the GUI is faster than full-text search.
- **Don't fight the reflection engine** — if it marks a chunk as
  superseded, trust it. Reflections are logged, so you can inspect
  what happened in Memory Health.
