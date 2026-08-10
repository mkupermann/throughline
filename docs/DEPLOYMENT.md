# Deployment Scenarios

Throughline is a local-first, single-user system. All scenarios below keep the
database and all session data on the user's machine. See
[SECURITY.md](../SECURITY.md) for the threat model before exposing anything
beyond localhost.

## 1. Docker Compose (recommended)

```bash
docker compose up -d
```

Starts:

- `postgres` — PostgreSQL 16 with pgvector. The schema in `sql/schema.sql` is
  applied automatically on first start of an empty volume. Data persists in the
  named volume `throughline_postgres_data`; do not delete it.
- `web` — web UI + JSON API on `http://127.0.0.1:8787`.

Host tool directories (`~/.claude`, `~/.cursor`, `~/.codex`, …) are mounted
read-only under `/root` inside the containers, where the adapters expect them.
A directory that does not exist on the host is simply reported as
"not present" — no configuration needed.

Optional profiles:

```bash
docker compose --profile mcp up -d          # MCP server (stdio; see below)
docker compose --profile embeddings up -d   # local Ollama for embeddings
```

The MCP server speaks stdio; MCP clients normally launch it themselves. For a
containerized client, use `docker compose run -i --rm mcp`.

Running an ingest inside the stack:

```bash
docker compose exec gui throughline ingest --all
```

Note for Linux hosts: the Cline mount in `docker-compose.yml` uses the macOS
VS Code path (`~/Library/Application Support/Code/...`). On Linux, change it to
`~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks`.

## 2. Native installation

```bash
pip install -r requirements.txt
pip install -e .
createdb throughline
psql throughline < sql/schema.sql
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
docker compose up -d postgres
export PGHOST=127.0.0.1 PGPORT=5433 PGDATABASE=throughline \
       PGUSER=throughline PGPASSWORD=throughline_dev_password
throughline ingest --all
```

The database is published on host port **5433** by default (override with
`THROUGHLINE_DB_PORT`) so an existing native PostgreSQL on 5432 is never
shadowed. This is also the setup the integration test suite expects.

## Backup and restore

The only state worth backing up is the PostgreSQL database:

```bash
# Docker
docker compose exec postgres pg_dump -U throughline throughline > backup.sql

# Native
pg_dump throughline > backup.sql
```

Restore into an empty database with `psql < backup.sql`. Source session files
remain owned by their respective tools; Throughline never modifies them
(read-only mounts in Docker, read-only access natively).

## What not to do

- Do not expose PostgreSQL or the GUI beyond localhost without adding
  authentication and TLS; neither ships with any.
- Do not run one shared Throughline database for multiple users — the schema
  and threat model are single-user by design.
- Do not change the default database password in `docker-compose.yml` alone —
  it is a development credential; if the port must be reachable from other
  machines, treat the whole deployment as out of scope of the current threat
  model.
