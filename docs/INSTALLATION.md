# Installation Guide

Full setup for a fresh machine. macOS is the primary supported platform.
Linux works for the core stack (Postgres, Python, Streamlit), but the
AppleScript-based mail and calendar hooks are macOS-only.

## 1. Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| macOS | 13 (Ventura) | 14 (Sonoma) or 15 (Sequoia) recommended |
| Homebrew | current | [brew.sh](https://brew.sh) |
| PostgreSQL | 16 | installed via Homebrew |
| Python | 3.10 | 3.11 or 3.12 recommended |
| Git | any | |

Optional but recommended:

| Tool | Purpose |
|---|---|
| Claude Code CLI | one of two memory-extraction backends (the other is the Anthropic API) — inherits your existing CLI authentication and configured model |
| Ollama | local embeddings (no API key needed) |
| OpenAI API key | alternative to Ollama for embeddings |
| Anthropic API key | alternative to Claude CLI for memory extraction |

## 2. Install PostgreSQL and pgvector

### PostgreSQL

```bash
brew install postgresql@16
brew services start postgresql@16
```

Verify it is running:

```bash
/opt/homebrew/opt/postgresql@16/bin/pg_isready
# /tmp:5432 - accepting connections
```

Add PostgreSQL to your `PATH` for convenience:

```bash
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### pgvector

Homebrew's `pgvector` bottle is built against the latest Postgres version,
which is usually one major version ahead of `postgresql@16`. The safest
approach is to compile pgvector against your installed Postgres:

```bash
cd /tmp
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector
make PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
make install PG_CONFIG=/opt/homebrew/opt/postgresql@16/bin/pg_config
```

## 3. Clone the repository

```bash
cd ~/Documents/GitHub   # or wherever you keep code
git clone https://github.com/mkupermann/throughline.git
cd throughline
```

## 4. Create the database and schema

```bash
createdb claude_memory
psql -d claude_memory -f sql/schema.sql
```

Verify 11 tables exist:

```bash
psql -d claude_memory -c "\dt"
```

You should see `conversations`, `messages`, `memory_chunks`, `skills`,
`prompts`, `projects`, `entities`, `relationships`, `entity_mentions`,
`embeddings`, `memory_reflections`, `ingestion_log`.

## 5. Install Python dependencies

```bash
pip3 install --break-system-packages -r requirements.txt
```

`--break-system-packages` is required on macOS with Homebrew Python because of
PEP 668. If you prefer a virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 6. Configure

Copy the example config and fill in what is relevant to your setup:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

Edit `.env` to set `PGUSER` (defaults to `$USER`) and optional API keys.

`config.yaml` is already gitignored.

## 7. First ingestion run

```bash
python3 scripts/ingest_sessions.py
python3 scripts/scan_skills.py
python3 scripts/scan_prompts.py
```

Expected output:

```
Claude Memory DB — Session Ingestion
============================================================
Gefunden: 47 JSONL-Dateien

Ergebnis:
  Ingestiert:  47 Sessions (3068 Messages)
  Übersprungen: 0
  Fehler:      0
```

Your numbers will vary.

## 8. Start the GUI

```bash
cd gui
streamlit run app.py
```

Open `http://localhost:8501`. You should land on the Dashboard with counts
for conversations, messages, skills, and memory chunks.

## 9. Optional — enable the scheduler

```bash
./scripts/install.sh
```

This installs three launchd jobs:

- `com.claude-memory-ingest` — hourly ingestion
- `com.claude-memory-extract` — daily 02:00 UTC memory extraction
- `com.claude-memory-backup` — daily 03:00 UTC `pg_dump` with 30-day retention

Verify:

```bash
launchctl list | grep claude-memory
```

## 10. Optional — install the context pre-loader

```bash
./scripts/install_hooks.sh
```

This registers a `SessionStart` hook in `~/.claude/settings.json` that injects
relevant memories into every new Claude session as
`./.claude/MEMORY_CONTEXT.md`.

## 11. Optional — generate embeddings for semantic search

Pick a backend:

**OpenAI** (cheapest, cloud):

```bash
export OPENAI_API_KEY=sk-...
python3 scripts/generate_embeddings.py --backend openai
```

**Ollama** (local, no API key):

```bash
brew install ollama
brew services start ollama
ollama pull nomic-embed-text
python3 scripts/generate_embeddings.py --backend ollama
```

Test semantic search:

```bash
python3 scripts/search_semantic.py "PostgreSQL migration"
```

## Troubleshooting

### `psql: error: connection to server failed`

PostgreSQL is not running or not on `localhost:5432`. Check:

```bash
brew services list | grep postgresql
/opt/homebrew/opt/postgresql@16/bin/pg_isready
```

Fix:

```bash
brew services restart postgresql@16
```

### `ERROR: extension "vector" is not available`

pgvector is not installed for the Postgres version you're running.
Rebuild it (see step 2). Verify with:

```bash
ls /opt/homebrew/opt/postgresql@16/share/postgresql@16/extension/vector*
```

### macOS TCC keeps prompting for permissions

Claude CLI updates itself frequently. Each update changes the binary path
and macOS re-prompts for file and automation access. Permanent fix:

**System Settings → Privacy & Security → Full Disk Access**, add the folder:

```
~/.local/share/claude
```

as a whole. This covers all future Claude updates.

Also add `/bin/bash` and `/usr/bin/osascript` there.

### Streamlit shows `psycopg2.InterfaceError: connection already closed`

The shared connection died (often after a long idle period). Refresh the
browser — the app has a reconnect-on-error fallback and will recover on the
next query.

### Schema migration after an upgrade

When the schema changes between versions:

```bash
psql -d claude_memory -f sql/schema.sql
```

The schema file uses `CREATE ... IF NOT EXISTS` everywhere, so re-running
it is safe. For schema migrations that require data changes, look for
files under `sql/migrations/`.
