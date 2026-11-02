# Installation Guide

Full setup for a fresh machine. Docker Compose is the shortest supported path,
and on Windows it is the only sensible one: the image carries PostgreSQL 16 and
pgvector already built, which is the part that is genuinely painful to
reproduce natively there. The native path is for an existing PostgreSQL
installation.

macOS and Linux are tested. Windows with Docker Desktop and the WSL2 backend is
not: the known platform assumptions have been removed — `os.getuid` in the
Compose bootstrap, the macOS-only Cline path, and a generated column that split
project paths on `/` alone and so turned every `C:\Users\…` session into its
own project — but removing the defects that were found is not the same as
having run it. AppleScript integrations are macOS only, and the launchd
scheduler status is macOS only.

## 1. Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| macOS | 13 (Ventura) | 14 (Sonoma) or 15 (Sequoia) recommended |
| Homebrew | current | [brew.sh](https://brew.sh) |
| PostgreSQL | 16 | installed via Homebrew |
| Python | 3.10+ | matches package metadata |
| Git | any | also needed by the git source, natively; the image brings its own |

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
python3 scripts/init_compose_env.py --check-docker
docker compose up -d
docker compose exec web throughline ingest
```

The bootstrap script creates or updates the ignored `.env`, generates a random
database password, and writes your numeric UID/GID. Compose uses those values
to run application containers as an unprivileged user while retaining access to
0600 source files on rootful Linux Docker without user-namespace remapping and Docker Desktop for macOS. PostgreSQL, the web
UI, and optional Ollama ports publish on loopback only. The migration service
runs before web or MCP starts; check it with `docker compose ps` if startup
does not complete.

Open `http://127.0.0.1:8788`. See [DEPLOYMENT.md](DEPLOYMENT.md) for profiles,
credential rotation, backups, and Linux source-mount details.

The image runs the Rust binary, whose `ingest` reads every source but web
pages; `--source <name>` picks one.

### Mounts for documents, Gemini CLI and Copilot Chat

The standard mounts cover the first nine AI tools and, read-only,
`~/Documents/GitHub` at `/home/throughline/Documents/GitHub`. While
`THROUGHLINE_PROJECTS_DIR` is unset, a git repository or Markdown folder in
there therefore needs no extra mount. Everything else goes into
`docker-compose.override.yml` next to `docker-compose.yml`. Compose reads that
file on its own, and git ignores it, because it holds your paths. Mount each
folder read-only at the place your `.env` names, with `~` standing for
`/home/throughline` ([USAGE.md](USAGE.md#documents-repositories-and-web-pages)
explains the settings):

```yaml
# docker-compose.override.yml
services:
  web:
    volumes:
      # Markdown folders and git repositories.
      - ~/notes:/home/throughline/notes:ro
      - ~/src/harbour-app:/home/throughline/src/harbour-app:ro
      # A folder outside your home keeps its absolute path on both sides.
      - /path/to/handbook:/path/to/handbook:ro
      # The URL list for THROUGHLINE_WEB_URLS.
      - ~/.config/throughline/web-urls.txt:/home/throughline/.config/throughline/web-urls.txt:ro
      # Gemini CLI: the session logs only, not the credentials next to them.
      - ~/.gemini/tmp:/home/throughline/.gemini/tmp:ro
      # Copilot Chat. These are the macOS folders; on Linux they start at
      # ~/.config/Code/User. The target is the Linux path either way.
      - ~/Library/Application Support/Code/User/workspaceStorage:/home/throughline/.config/Code/User/workspaceStorage:ro
      - ~/Library/Application Support/Code/User/globalStorage/emptyWindowChatSessions:/home/throughline/.config/Code/User/globalStorage/emptyWindowChatSessions:ro
```

The matching `.env` lines for that example:

```bash
THROUGHLINE_MARKDOWN_ROOTS=~/notes:/path/to/handbook
THROUGHLINE_GIT_ROOTS=~/src/harbour-app
THROUGHLINE_WEB_URLS=~/.config/throughline/web-urls.txt
```

Apply it with `docker compose up -d web`; a plain restart keeps the old
mounts and environment. If `.env` sets `COMPOSE_FILE`, Compose skips the
override file unless the list names it, for example
`COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml`. Afterwards
`docker compose exec web throughline doctor` lists under `sources:` what the
container can see; a Markdown root reported as not found is usually a missing
mount. Roots need distinct folder names: of two roots with the same name, the
second is skipped with a warning ([USAGE.md](USAGE.md#markdown-folders)).

### An extra root CA

The image verifies TLS against its own certificate store. Behind a proxy that
inspects TLS, `cargo` cannot download crates during the build, and every HTTPS
fetch of the web source fails with a certificate error, until the image trusts
the proxy's root CA. The build takes one from a build context named
`extra_ca`: a folder of its own that holds the CA as PEM files named `*.crt`.
Without that context it adds nothing. With Compose, in the same override file,
for every service you build (`mcp` too, if you use that profile):

```yaml
services:
  migrate:
    build:
      additional_contexts:
        extra_ca: /path/to/ca-folder
  web:
    build:
      additional_contexts:
        extra_ca: /path/to/ca-folder
```

Then run `docker compose build && docker compose up -d`. A plain `docker
build` takes `--build-context extra_ca=/path/to/ca-folder`. Adding or changing
a certificate rebuilds what depends on it; after removing the context, build
once with `--no-cache`, or the cached layer keeps the old CA.

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

The Rust runtime (`rust/target/release/throughline`, see section 10) has
migrations of its own: columns the Rust extraction reads and the `document`
role of the Markdown, git and web sources. After building it, and after every
upgrade, run `rust/target/release/throughline migrate` once. The scheduled
jobs of section 10 run it before each Rust step; it does nothing on a current
schema. Without it the Rust `ingest` and `extract-memory` stop with one line,
``the database lacks migration …: run the Rust `throughline migrate` (rust/target/release/throughline migrate, or `docker compose run --rm migrate`)``.

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

This loads every plist in `launchd/`. The scheduled jobs are:

- `com.claude-memory-ingest` — hourly ingestion. After the tool sessions it
  runs the Rust binary in `rust/target/release/`: `migrate`, then
  `--source gemini_cli`, `--source copilot_chat`, `--source markdown` and
  `--source git`; set `THROUGHLINE_MARKDOWN_ROOTS` and `THROUGHLINE_GIT_ROOTS`
  in the installed plist (empty, both write nothing; distinct root folder
  names). `scripts/install.sh`
  writes every plist anew from its template, so a second run empties them
  again; set them once more afterwards. Web pages stay manual:
  `throughline ingest --source web` is the only step that sends requests out.
- `com.claude-memory-extract` — daily 02:00 UTC memory extraction with the
  Rust binary in `rust/target/release/` (`migrate`, then `extract-memory`, 20
  conversations per run). The document sources and the `document` profile exist only in the Rust
  runtime; the Python extractor reads only user and assistant messages and
  would leave every document unextracted. Set `OLLAMA_HOST`,
  `OLLAMA_EMBED_MODEL` and any model setting the server uses in the installed
  plist.
- `com.claude-memory-backup` — daily 03:00 UTC `pg_dump` with 30-day retention
- `com.claude-memory-embed` — hourly at minute 15, `throughline embed --limit 10000`:
  vectors for notes the write itself could not embed, then for messages. It
  runs the Rust binary in `rust/target/release/`, `migrate` first (the Python CLI's `embed`
  ignores `OLLAMA_EMBED_MODEL`). Set `OLLAMA_EMBED_MODEL` and
  `OLLAMA_HOST` in the installed plist to the server's values.

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

Embeddings come from a local Ollama, no API key. Hosted OpenAI embeddings are
disabled.

```bash
brew install ollama
brew services start ollama
ollama pull nomic-embed-text          # embeddings, the default model, ~275 MB
ollama pull qwen3.5:9b                # generation, ~6.6 GB
throughline embed                     # up to 500 per run; --limit <n> changes that
```

For notes in more than one language the multilingual model is the measured
recommendation. It is opt-in: pull it and name it in `.env`.

```bash
ollama pull granite-embedding:278m
# in .env:
OLLAMA_EMBED_MODEL=granite-embedding:278m
```

If `ollama pull` fails, import the same model from Docker Hub instead: fetch
`ai/granite-embedding-multilingual:278M-F16`, run
`ollama create granite-embedding-multilingual:278m -f Modelfile` and set
`OLLAMA_EMBED_MODEL=granite-embedding-multilingual:278m`. The steps are in
[USAGE.md](USAGE.md#when-ollama-pull-fails). An install that already has
vectors should switch through the target model described in
[USAGE.md](USAGE.md#switching-models-without-losing-recall) instead.

On Windows the session directories live under `C:\Users\<you>\` and Docker
Desktop must be allowed to share that drive. `make init-compose` resolves
Cline's storage location for the platform and writes it to `.env` as
`THROUGHLINE_CLINE_DIR`; where Cline is not installed it creates an empty
placeholder, because Docker refuses to bind-mount a source path that does not
exist.

Two models, two jobs. An embedding model cannot answer a question or extract
memory, so a machine with only an embedding model has working semantic search
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
