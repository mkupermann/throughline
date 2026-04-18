# FAQ

## General

### Who is this for?

Developers who use Claude Code daily and want their past sessions to
inform future sessions without copy-pasting context every time. If you
have dozens of active projects, this tool keeps each one's history
queryable instead of buried in JSONL files.

### Is it a replacement for Claude's built-in memory?

No — Claude's built-in memory is per-session and per-project. This tool
is the cross-session, cross-project layer underneath.

### Can I use it without Claude Code?

The database schema and GUI work standalone. You can insert memory
chunks manually, edit them in the GUI, and query them via SQL or
semantic search. But the ingestion pipeline is specific to Claude
Code's JSONL format.

### Does it send data anywhere?

Not by default. Everything runs on localhost:
- PostgreSQL on `localhost:5432`
- Streamlit on `localhost:8501`

Optional network use, opt-in per feature:
- OpenAI API for embeddings — only if you pick the `openai` backend
  (default is local Ollama)
- Memory extraction sends conversation windows to whichever backend you
  configured — the Anthropic API (`ANTHROPIC_API_KEY`) or the local Claude
  Code CLI in headless mode

## Installation

### Why PostgreSQL and not SQLite?

- `pgvector` — mature, high-performance vector search
- `jsonb` — schemaless columns for flexible metadata
- Concurrent writes — multiple scripts can ingest in parallel
- `tsvector` / `pg_trgm` — full-text and trigram search for free

SQLite's `sqlite-vec` extension is catching up, but the feature delta
still matters for this workload.

### I'm on Linux / Windows. Does it work?

- **Linux:** the core (Postgres, scripts, Streamlit) works fine. The
  AppleScript hooks for Mail / Calendar do not. launchd does not exist
  — use systemd user services or cron instead.
- **Windows:** untested. PostgreSQL, Python, and Streamlit all run on
  Windows, so the core should work via WSL2 or natively. AppleScript
  and launchd do not exist.

Contributions to add Linux systemd units and a Windows task-scheduler
equivalent are welcome.

### Why do I need Homebrew?

Only because the installer uses `brew install postgresql@16 pgvector`.
If you install those some other way (from source, MacPorts, Postgres.app),
just skip the install script and run the SQL schema manually.

## Usage

### The scheduler ran but no memory chunks were extracted

Three common causes:

1. **No Claude CLI and no `ANTHROPIC_API_KEY`** — the extraction script
   requires one of them. Check with `which claude` and `echo $ANTHROPIC_API_KEY`.

2. **Conversations have fewer than 5 messages** — the default threshold
   skips trivially short sessions. Lower it in
   `scripts/extract_memory.py` if you want them processed.

3. **Conversations already processed** — the script tracks per-source
   processing. Force re-extraction with
   `DELETE FROM memory_chunks WHERE source_id = <id>` then re-run.

### I want to delete a memory chunk permanently

Open the chunk in the GUI (Memory page → click the card) and use the
Delete button. The row is hard-deleted. If you want to preserve audit
history, use the `status` column instead — set it to `superseded` or
`archived` so the row stays queryable but does not show up in active
views.

### Why are all my skills showing today's date in the calendar?

First scan — `scan_skills.py` stores `file_modified` from the
filesystem's `mtime`. If you just cloned the repo or copied skills
between machines, all `mtime` values will be recent. Re-scan after
letting the skills settle, or accept that scan-time is "good enough"
for the calendar.

### How do I back up the database?

The `backup.sh` script runs daily via launchd and writes compressed
dumps to `~/.local/share/claude-memory/backups/` with 30-day retention.
Manually:

```bash
pg_dump claude_memory | gzip > ~/backup.sql.gz
```

To restore:

```bash
dropdb claude_memory
createdb claude_memory
gunzip -c ~/backup.sql.gz | psql claude_memory
```

### Can I edit a memory chunk by hand?

Yes. In the GUI, open the chunk and edit in-place. Content, tags,
confidence, category — all editable. Changes write immediately.

## Data Model

### What is the difference between a message and a memory chunk?

- **message** — one turn in a Claude conversation, stored verbatim
- **memory chunk** — a distilled fact extracted from one or many
  messages, with a category, confidence, and optional expiration

A conversation might have 500 messages that produce only 3–4 memory
chunks. Messages are preserved so you can always look up the original
context; chunks are the distilled knowledge layer on top.

### Why are there two places for skills — the filesystem and the database?

The filesystem (`~/.claude/skills/`) is Claude's source of truth — that
is where Claude actually reads skills from. The database is a
denormalized index of them, with use counts and timestamps, so the GUI
and calendar have something to query against. `scan_skills.py` keeps
the two in sync.

### What are entities and relationships?

The knowledge graph tables. Entities are people, projects,
technologies, decisions, concepts, and organizations that Claude
mentions in conversations. Relationships connect them ("Jane works on
Project Alpha", "Project Alpha depends on pgvector"). The graph is
built by `scripts/extract_entities.py` running Claude over each
conversation.

## Privacy and security

### What sensitive data could end up in my database?

Claude Code sessions can include file paths, source code snippets, API
keys printed by debug tools, email addresses, project names, contacts
mentioned in chat. Treat the database as confidential.

See `SECURITY.md` for the full threat model and hardening recommendations.

### Does anything scrub secrets before they get sent to Claude for extraction?

Yes. Every transcript runs through a heuristic redaction pass
(`throughline/pii.py`) before it is sent to Claude. API keys, JWTs, bearer
tokens, `password=` / `secret=` / `token=` assignments, private-key blocks,
email addresses, and home-directory usernames are all replaced with short
`<REDACTED_*>` markers. On by default; disable with
`THROUGHLINE_REDACT_PII=0` (or `memory.redact_pii: false` in `config.yaml`).

### Should I encrypt the database?

For a single-user local setup: probably not required if your disk is
FileVault-encrypted (macOS) or LUKS-encrypted (Linux). The entire
PostgreSQL data directory sits inside that encrypted volume.

For backups to cloud storage: yes, always encrypt. `age` is the simplest
tool — `age -e -r $(cat ~/.config/age/recipient) backup.sql.gz > backup.sql.gz.age`.

## Contributing and support

### How do I report a bug?

Open a GitHub issue with:
- The command you ran
- The full traceback (for Python) or stderr (for scripts)
- Your OS and Postgres version
- Whether the same command worked before

For security bugs, use GitHub's private advisory form (see SECURITY.md).

### Can I fork this?

Yes, MIT license. If you publish a fork, a link back is appreciated but
not required.

### What is on the roadmap?

- MCP server integration so Claude can query memory via the MCP
  protocol, not just the skill
- Linux systemd equivalents of the launchd jobs
- Multi-user schemas (each user gets a schema namespace)
- Export adapters for Obsidian and Notion
- Web-first deployment mode (for self-hosting on a home server)

If you want one of these, open an issue.
