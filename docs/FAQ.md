# FAQ

## General

### Who is this for?

Developers who use several local AI coding tools and want one searchable memory
layer across their session histories.

### Is it a replacement for an assistant's built-in memory?

No. Throughline is a local cross-session, cross-project store. It can ingest
Claude Code, Codex, Cursor, Zed, Hermes, Continue, Cline, Windsurf, and Vibe.

### Can I use it without Claude Code?

Yes. The UI, CLI, MCP server, and database do not require Claude Code. Each
adapter only reads its own tool's on-disk format; `throughline ingest
--list-sources` shows which sources are present.

### Does it send data anywhere?

Not by default. PostgreSQL runs on a native local port, normally
`localhost:5432`, or on the Docker loopback port, normally `127.0.0.1:5433`.
The web UI listens on `127.0.0.1:8790` natively or `127.0.0.1:8788` in Docker.

Answers, extraction, titles, reflection, and embeddings can call a model.
Choose a local backend to keep content on the machine. A hosted backend receives
the relevant excerpts or transcripts. [SECURITY.md](../SECURITY.md) describes
the boundaries and redaction controls.

## Installation

### Why PostgreSQL instead of SQLite?

Throughline uses pgvector, JSONB, full-text/trigram indexes, and transactions
that protect concurrent ingestion and derived data. PostgreSQL provides those
in the same database as the sessions and memory.

### Does Linux or Windows work?

Linux supports the core stack and ships user-level systemd timers for ingest,
extraction, and backup. Native Windows works the same way via Task Scheduler
— see `windows/README.md` for `register-tasks.ps1`, which schedules the same
three jobs. AppleScript integrations remain macOS only, and Windows under
WSL2 is not tested — use the native route, not WSL2, for the scheduled
tasks.

### Do I need Homebrew?

Only for the native macOS route that uses Homebrew's PostgreSQL packages. The
Docker route does not. With another PostgreSQL installation, create the database
and run `throughline migrate`.

## Operations

### How do schema upgrades work?

Run `throughline migrate --status`, then `throughline migrate`. The migrations
are packaged with the installed wheel. Docker Compose runs the same command
automatically and waits for it before starting the web or MCP service. Do not
initialize a new database by applying `sql/schema.sql` directly.

### The scheduler ran but no memory chunks were extracted

Check that a model backend is available and that the selected conversations are
eligible for extraction. Run `throughline doctor`, then start a foreground
`throughline extract-memory` to see the error. Do not delete database rows as a
troubleshooting shortcut.

### How do I back up and restore the database?

Run `throughline backup` for the configured private local dump path. To restore
a manual dump into an empty native database:

```bash
createdb throughline
psql throughline < backup.sql
throughline migrate
```

Source session files remain owned by their AI tools. Throughline never modifies
them.

### Can I run Docker after changing the Compose password?

For a new volume, run `python3 scripts/init_compose_env.py` before
`docker compose up -d`. An existing PostgreSQL volume retains its role and
database name. Its password must be rotated with the explicit
`credential-rotate` profile in [DEPLOYMENT.md](DEPLOYMENT.md); changing
`POSTGRES_USER` or `POSTGRES_DB` does not rename either one.

## Data and privacy

### What is the difference between a message and a memory chunk?

A message is a stored turn from a session. A memory chunk is a distilled fact
with a category, confidence, tags, and source reference. Messages keep the
original context; chunks make durable information easier to retrieve.

### Can I edit or remove a memory chunk?

Use Curate in the web UI or the MCP tools. Forgetting is an explicit destructive
operation, so it asks for confirmation and preserves an audit record where the
schema permits it.

### What sensitive data can be stored?

AI-tool sessions can include file paths, source code, output containing API
keys, email addresses, project names, and contacts. Treat the database and its
backups as confidential. Use disk encryption and encrypt any backup that leaves
the machine.

### Does Throughline redact secrets before model calls?

Extraction uses the heuristic redaction pass in `throughline/pii.py` by default.
It recognises common API-key shapes, JWTs, bearer tokens, explicit credential
assignments, private-key blocks, email addresses, and home-directory usernames.
`THROUGHLINE_REDACT_PII=0` disables this for extraction. `THROUGHLINE_REDACT_PROMPTS=1`
redacts answer excerpts sent to a remote answering model.

## Support

### How do I report a bug?

Open an issue with the command, full stderr or traceback, OS, PostgreSQL version,
Python version, and installation route. For security issues, use GitHub's private
security advisory flow described in [SECURITY.md](../SECURITY.md).
