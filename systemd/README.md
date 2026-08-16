# Throughline systemd units (Linux)

User-level `systemd` units that mirror the macOS `launchd` setup in `../launchd/`.
No root, no system-wide install — everything lives under `~/.config/systemd/user/`.

## What runs when

| Unit | Schedule | Purpose |
|------|----------|---------|
| `throughline-ingest.timer`  | hourly           | `scripts/ingest_sessions.py` — pull new Claude Code JSONL sessions into Postgres |
| `throughline-extract.timer` | daily at 02:00   | `scripts/extract_memory.py` — distil memory chunks from recent conversations    |
| `throughline-backup.timer`  | daily at 03:00   | `scripts/backup.sh` — `pg_dump` the `throughline` database                      |

All three services are `Type=oneshot`: they run, finish, and exit. The timer units keep
them on schedule.

## Prerequisites

- Linux with `systemd --user` support (any mainstream distro from the last five years).
- `python3` available at `/usr/bin/python3` (adjust the `ExecStart` line if yours differs).
- A running `postgres` with the `throughline` database and the schema from `sql/schema.sql`
  applied. Either run it locally, or start the container from `docker-compose.yml`.
- Your checkout of Throughline at `~/.local/share/throughline/` (or update the paths in each
  unit file — `%h` expands to `$HOME`, `%u` to your username).

## Install

```bash
# 1) Make the scripts reachable at the path the units expect.
mkdir -p ~/.local/share
ln -sfn "$PWD" ~/.local/share/throughline      # or copy, if you prefer

# 2) Install the unit files.
mkdir -p ~/.config/systemd/user
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/

# 3) Install one shared database configuration for all three jobs.
mkdir -p ~/.config/throughline
install -m 600 systemd/throughline.env ~/.config/throughline/throughline.env

# 4) Tell systemd about the new units.
systemctl --user daemon-reload

# 5) Enable + start the three timers.
systemctl --user enable --now \
    throughline-ingest.timer \
    throughline-extract.timer \
    throughline-backup.timer
```

To have the timers keep running after you log out, enable user lingering once:

```bash
sudo loginctl enable-linger "$USER"
```

## Verify

```bash
# List all Throughline timers with their next run time.
systemctl --user list-timers 'throughline-*'

# Trigger an ingest run manually (does not wait for the timer).
systemctl --user start throughline-ingest.service

# Tail the journal for a service.
journalctl --user -u throughline-ingest.service -f
```

## Configuration

All three services read `~/.config/throughline/throughline.env`, which owns
`PGDATABASE`, `PGHOST`, and `PGPORT`. `PGUSER` remains the systemd user. Add
`PGPASSWORD` to the shared file when password authentication is required:

```bash
nano ~/.config/throughline/throughline.env
```

For example:

```ini
PGHOST=db.example.internal
PGPASSWORD=changeme
```

The shared file is not overwritten by unit updates.

The shipped default is `PGDATABASE=throughline`. To keep a legacy
`claude_memory` database instead, change the one shared file; all ingest,
extract, and backup jobs then use it:

```ini
PGDATABASE=claude_memory
```

## Uninstall

```bash
systemctl --user disable --now \
    throughline-ingest.timer \
    throughline-extract.timer \
    throughline-backup.timer
rm ~/.config/systemd/user/throughline-*.{service,timer}
systemctl --user daemon-reload
```
