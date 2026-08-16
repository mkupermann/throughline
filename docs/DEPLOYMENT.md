# Deployment Scenarios

Throughline is a local-first, single-user system. All scenarios below keep the
database and all session data on the user's machine. See
[SECURITY.md](../SECURITY.md) for the threat model before exposing anything
beyond localhost.

## 1. Docker Compose (recommended)

```bash
python3 scripts/init_compose_env.py
docker compose config --quiet
docker compose up -d
```

Starts:

- `postgres` — PostgreSQL 16 with pgvector. Data persists in the named volume
  `throughline_postgres_data`; do not delete it.
- `migrate` — runs the packaged `throughline migrate` command after PostgreSQL
  is ready. Both `web` and the optional `mcp` service wait for it to finish
  successfully, on a fresh or existing volume.
- `web` — web UI + JSON API on `http://127.0.0.1:8788`. The container listens
  on 8787 internally; only the published host port differs.

`scripts/init_compose_env.py` creates or updates the ignored `.env` with a
random database password plus your numeric UID/GID. The image remains
unprivileged, and that matching identity can read 0600 source files on Linux
and Docker Desktop for macOS. Re-run it after moving the checkout to another
user, then rebuild with `docker compose build`.

Host tool directories (`~/.claude`, `~/.cursor`, `~/.codex`, …) are mounted
read-only under `/home/throughline`, which is the application user's `$HOME`
and therefore the path the adapters resolve. A directory that does not exist
on the host is simply reported as "not present" — no configuration needed.

Optional profiles:

```bash
docker compose --profile mcp up -d          # MCP server (stdio; see below)
docker compose --profile embeddings up -d   # local Ollama for embeddings
```

The MCP server speaks stdio; MCP clients normally launch it themselves. For a
containerized client, use `docker compose run -i --rm mcp`.

Running an ingest inside the stack:

```bash
docker compose exec web throughline ingest --all
```

Note for Linux hosts: the Cline mount in `docker-compose.yml` uses the macOS
VS Code path (`~/Library/Application Support/Code/...`). On Linux, change it to
`~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks`.

## 2. Native installation

```bash
pip install -r requirements.txt
pip install -e .
createdb throughline
throughline migrate
throughline ingest --all
```

Database connection is taken from the standard `PG*` environment variables
(`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`), with a `.env` file
supported in the repository root. See [INSTALLATION.md](INSTALLATION.md).

## 3. Scheduled ingestion

Memory is most useful when it is current. Schedule `throughline ingest --all`
and the extraction pipeline:

- **macOS (launchd)** — see [INSTALLATION.md](INSTALLATION.md) for the plist
  examples used on macOS.
- **Linux (systemd)** — ready-made units are provided in
  [`systemd/`](../systemd/): `throughline-ingest.timer`,
  `throughline-extract.timer`, and `throughline-backup.timer`.

A sensible default cadence is ingest every 15–30 minutes, extraction hourly,
backup daily.

## 4. Hybrid: native tools, Dockerized database

Run only PostgreSQL in Docker and everything else natively:

```bash
python3 scripts/init_compose_env.py
docker compose up -d postgres
export PGHOST=127.0.0.1 PGPORT=5433
export PGDATABASE="$(grep '^POSTGRES_DB=' .env | cut -d= -f2-)"
export PGUSER="$(grep '^POSTGRES_USER=' .env | cut -d= -f2-)"
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
throughline migrate
throughline ingest --all
```

The database is published on host port **5433** by default (override with
`THROUGHLINE_DB_PORT`) so an existing native PostgreSQL on 5432 is never
shadowed. This is also the setup the integration test suite expects.

## Backup and restore

The only state worth backing up is the PostgreSQL database. The packaged backup
command creates its directory and dump files with owner-only permissions. In
Compose, dumps live in the persistent named volume `throughline_backup_data`
at `/var/lib/throughline/backups` in the `web` container, so replacing the web
container does not discard them:

```bash
# Docker
docker compose exec web throughline backup

# Native
throughline backup
```

The logical dumps are gzip-compressed SQL. Restore into an empty database with
`gzip -dc backup.sql.gz | psql`, then run `throughline migrate` to ensure the
schema is current. `throughline backup` finds `pg_dump` on `PATH`; set
`PG_DUMP_BIN` to an explicit executable or `PG_BIN` to its directory on unusual
native installations. Use the corresponding `pg_restore` from `PATH`,
`PG_RESTORE_BIN`, or `PG_BIN` for custom-format dumps made outside Throughline.
Source session files
remain owned by their respective tools; Throughline never modifies them
(read-only mounts in Docker, read-only access natively).

## Existing Compose volume: rotate a legacy password

PostgreSQL only applies `POSTGRES_PASSWORD` while initializing an empty data
directory. If this checkout used the old fixed Compose password and you now
set a new one in `.env`, rotate the existing role before starting migration-
gated services:

```bash
python3 scripts/init_compose_env.py
docker compose up -d postgres
THROUGHLINE_LEGACY_DB_PASSWORD='the-old-password' \
  docker compose --profile credential-rotate run --rm credential-rotate
docker compose up -d
```

The one-off service connects using only the supplied legacy password and
updates `CURRENT_USER` to the password in `.env`; it does not run during normal
startup. Use `THROUGHLINE_LEGACY_DB_USER` as well if the legacy role was not
`throughline`, and `THROUGHLINE_LEGACY_DB_NAME` if its database name was not
`throughline`. `POSTGRES_USER` and `POSTGRES_DB` are immutable once a volume
exists: keep their `.env` values equal to the existing role/database. The
rotator refuses a mismatch before connecting, rather than claiming it renamed
anything. If `throughline doctor` reports an authentication failure after a
Compose credential change, run this command before retrying `docker compose up
-d`.

## What not to do

- Do not expose PostgreSQL or the GUI beyond localhost without adding
  authentication and TLS; neither ships with any.
- Do not run one shared Throughline database for multiple users — the schema
  and threat model are single-user by design.
- Do not expose PostgreSQL or the GUI beyond localhost. A random local
  password is not a substitute for authentication and TLS on a network.
