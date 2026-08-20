# Installation Guide

Full setup for a fresh machine. Docker Compose is the shortest supported path.
The native path is for an existing PostgreSQL installation. macOS and Linux are
tested; Windows under WSL2 is not tested. AppleScript integrations are macOS
only.

## 1. Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| macOS | 13 (Ventura) | 14 (Sonoma) or 15 (Sequoia) recommended |
| Homebrew | current | [brew.sh](https://brew.sh) |
| PostgreSQL | 16 | installed via Homebrew |
| Python | 3.10+ | matches package metadata |
| Git | any | |

Optional but recommended:

| Tool | Purpose |
|---|---|
| Model backend | optional for answers, extraction, titles, and reflection; use a local backend to keep content on the machine |
| Ollama | local embeddings and model backend, no API key needed |
| OpenAI API key | optional hosted embedding or model backend |
| Claude Code CLI | optional model backend when configured locally |

## 2. Docker Compose

Docker with Compose v2 needs no native PostgreSQL installation. It does require
Python 3 to run the supplied Compose bootstrap script; the application itself
runs in the container:

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
python3 scripts/init_compose_env.py
docker compose up -d
docker compose exec web throughline ingest --all
```

The bootstrap script creates or updates the ignored `.env`, generates a random
database password, and writes your numeric UID/GID. Compose uses those values
to run application containers as an unprivileged user while retaining access to
0600 source files on Linux and Docker Desktop for macOS. PostgreSQL, the web
UI, and optional Ollama ports publish on loopback only. The migration service
runs before web or MCP starts; check it with `docker compose ps` if startup
does not complete.

Open `http://127.0.0.1:8788`. See [DEPLOYMENT.md](DEPLOYMENT.md) for profiles,
credential rotation, backups, and Linux source-mount details.

## 3. Install PostgreSQL and pgvector for the native route

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

## 4. Clone the repository

```bash
cd ~/Documents/GitHub   # or wherever you keep code
git clone https://github.com/mkupermann/throughline.git
cd throughline
```

## 5. Create the database and apply migrations

```bash
createdb throughline
```

Do not bootstrap a new database by applying `sql/schema.sql` directly. The next
step runs the tracked migrations.

## 6. Install Throughline

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
throughline migrate
```

The package requires Python 3.10 or later. `pip install -e .` includes the web
server and the built frontend, so Node is not needed for installation.

Verify the migration state:

```bash
throughline migrate --status
```

## 7. Configure the native connection

The standard libpq variables select the database. A repository-root `.env` is
loaded automatically:

```bash
cp .env.example .env
```

Set `PGDATABASE=throughline` for the new default, or retain
`PGDATABASE=claude_memory` when upgrading an existing installation. Set the
other `PG*` variables if your PostgreSQL requires them. Leave `PGUSER` unset
for a local native database unless it differs from your login user: Throughline
then resolves the actual login user. Do not put `PGUSER=$USER` in `.env`;
dotenv files do not expand shell variables.

## 8. First ingestion run

```bash
throughline ingest --list-sources
throughline ingest --all
throughline scan-skills
throughline scan-prompts
```

## 9. Start the web UI

```bash
throughline serve
```

Open `http://127.0.0.1:8790`. The current UI has eight routes: Overview, Find,
Timeline, Curate, Project, Detail, Operate, and Console. The navigation shows
the first six user-facing surfaces; project and detail pages open from links.

## 10. Optional: enable the scheduler

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

## 11. Optional: install the context pre-loader

```bash
throughline install-hooks
```

This registers a `SessionStart` hook in `~/.claude/settings.json` that injects
relevant memories into every new Claude session as
`./.claude/MEMORY_CONTEXT.md`.

## 12. Optional: generate embeddings for semantic search

Pick a backend:

**OpenAI** (cheapest, cloud):

```bash
export OPENAI_API_KEY=sk-...
throughline embed --backend openai
```

**Ollama** (local, no API key):

```bash
brew install ollama
brew services start ollama
ollama pull nomic-embed-text          # embeddings, ~275 MB
ollama pull qwen3.5:9b                # generation, ~6.6 GB
throughline embed --backend ollama
```

Two models, two jobs. An embedding model cannot answer a question or extract
memory, so a machine with only `nomic-embed-text` has working semantic search
and nothing else — `throughline doctor` reports both backends separately for
exactly that reason.

`qwen3.5:9b` needs roughly 8 GB of free RAM. On a smaller machine pull
`qwen3.5:4b` instead and set `THROUGHLINE_ANSWER_MODEL=qwen3.5:4b`; on a larger
one `qwen3.5:27b` is better at extraction and wants about 20 GB. Whatever you
pull is used — the default is a preference, not a requirement.

Test semantic search:

```bash
throughline search "PostgreSQL migration"
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
Rebuild it (see step 3). Verify with:

```bash
ls /opt/homebrew/opt/postgresql@16/share/postgresql@16/extension/vector*
```

### macOS TCC keeps prompting for permissions

Claude CLI updates itself frequently. Each update changes the binary path
and macOS re-prompts for file and automation access. Permanent fix:

**System Settings → Privacy & Security → Full Disk Access**, add the folder:

```text
~/.local/share/claude
```

as a whole. This covers all future Claude updates.

Also add `/bin/bash` and `/usr/bin/osascript` there.

### the web UI shows `psycopg2.InterfaceError: connection already closed`

The shared connection died (often after a long idle period). Refresh the
browser — the app has a reconnect-on-error fallback and will recover on the
next query.

### Schema migration after an upgrade

When the schema changes between versions:

```bash
throughline migrate --status
throughline migrate
```

Migrations are ordered SQL files shipped inside the installed package. Compose
runs the same command automatically and blocks web/MCP startup until it
succeeds. For an older database initialized from `sql/schema.sql`, the runner
records its baseline before applying later migrations. It does not rewrite or
delete migration history.
