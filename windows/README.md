# Throughline on Windows Task Scheduler

Windows equivalent of `../systemd/` (Linux) and `../launchd/` (macOS): no
admin rights, no root — everything runs as scheduled tasks under your own
Windows account, matching the `systemd --user` / launchd per-user-agent
model those two use.

Before this, Windows had no scheduler integration at all (`docs/FAQ.md`
said so directly), which meant Overview and Curate only stayed current if
you remembered to open Operate and click Run yourself.

## What runs when

| Task               | Schedule       | Purpose                                                          |
|---------------------|----------------|-------------------------------------------------------------------|
| Throughline Ingest  | hourly         | Pull available local-tool sessions into Postgres                  |
| Throughline Extract | daily at 02:00 | Distil memory chunks from recent conversations                    |
| Throughline Backup  | daily at 03:00 | Dump the database, verified and rotated                           |

## Two setups, auto-detected

Each script checks for a running `throughline-web` Docker container first
(`docker ps`) and picks its command accordingly — nothing to configure, and
the two install methods need genuinely different commands:

**Docker Compose** (`docker compose up` — what `docker-compose.yml` in the
repo root sets up): each script runs `docker exec throughline-web
throughline <command>`. No local PostgreSQL client or Python install needed
— the container already has both, plus its own environment. Backup uses the
CLI's own `throughline backup`, which works here because the container is
Linux and has bash.

**Native install** (`pip install throughline` against a PostgreSQL on this
machine): each script loads `%USERPROFILE%\.throughline\throughline.env`
(see `_env.ps1`) and calls the packaged CLI directly — `throughline ingest
--all`, `throughline extract-memory`. Backup is the one place this differs
from the Docker path: the CLI's own `backup` subcommand
(`throughline/cli.py:cmd_backup`) shells out to `bash scripts/backup.sh`,
which a native Windows install has no reason to have on `PATH`. For that
case only, `throughline-backup.ps1` reimplements the same dump/verify/rotate
sequence natively in PowerShell, using `pg_dump -Fc` (already compressed)
instead of the `pg_dump | gzip` pipeline the shell version uses.

## Prerequisites

**Docker Compose:** the stack already running (`docker compose up -d` from
the repo root) — nothing else.

**Native install:**

- Throughline installed, with `throughline` resolving in an ordinary
  PowerShell prompt (`Get-Command throughline`).
- `pg_dump` and `pg_restore` on `PATH` (they ship with the PostgreSQL
  installer, usually under `...\PostgreSQL\<version>\bin`).
- A running PostgreSQL with the `throughline` database, migrated
  (`throughline migrate`).

## Install

```powershell
cd windows
.\register-tasks.ps1
```

Under Docker Compose, this just registers the three tasks — there is no env
file to write. Under a native install, it also writes
`%USERPROFILE%\.throughline\throughline.env` from `throughline.env.example`
(only if that file doesn't exist yet — it never overwrites a config you've
already tuned).

## Verify

```powershell
# Next run time and last result for all three.
Get-ScheduledTask -TaskName "Throughline *" | Get-ScheduledTaskInfo

# Trigger one manually without waiting for its schedule.
Start-ScheduledTask -TaskName "Throughline Ingest"

# Task Scheduler GUI, if you'd rather look: taskschd.msc,
# under Task Scheduler Library (top level — these are not in a subfolder).
```

## Configuration (native install only)

Under Docker Compose the container already carries its own configuration —
nothing here applies. For a native install, every script falls back to
loading `%USERPROFILE%\.throughline\throughline.env` (`_env.ps1` does this),
which owns `PGDATABASE`, `PGHOST`, `PGPORT` and, optionally, `PGPASSWORD`.
Same three keys as `systemd/throughline.env` and the launchd plists, so a
setup already tuned on Linux or macOS carries over without translation:

```ini
PGHOST=db.example.internal
PGPASSWORD=changeme
```

Backup destination and retention (native path's `throughline-backup.ps1`
only — the Docker path's `throughline backup` writes into the `backup_data`
volume `docker-compose.yml` already defines):

```powershell
$env:CLAUDE_MEMORY_BACKUP_DIR = "D:\Backups\throughline"   # default: %LOCALAPPDATA%\throughline\backups
```

(Set as a machine or user environment variable if you want it to apply to
the scheduled run, not just your current shell — `_env.ps1` only sets
`PGDATABASE`/`PGHOST`/`PGPORT`/`PGPASSWORD` from the file; other variables
follow the normal Windows environment.)

## Uninstall

```powershell
cd windows
.\unregister-tasks.ps1
```

Leaves `%USERPROFILE%\.throughline\throughline.env` in place — it's your
database config, not scheduler state.
