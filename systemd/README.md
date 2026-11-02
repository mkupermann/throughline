# Throughline systemd units (Linux)

User-level `systemd` units that mirror the macOS `launchd` setup in `../launchd/`.
No root, no system-wide install — everything lives under `~/.config/systemd/user/`.

## What runs when

| Unit | Schedule | Purpose |
|------|----------|---------|
| `throughline-ingest.timer`  | hourly           | `throughline ingest --all` — pull available local-tool sessions into Postgres; then the Rust `migrate` and `ingest --source` for `gemini_cli`, `copilot_chat`, `markdown` and `git` |
| `throughline-extract.timer` | daily at 02:00   | Rust `throughline migrate`, then `extract-memory` — notes from conversations not yet extracted, 20 per run, documents included |
| `throughline-backup.timer`  | daily at 03:00   | `scripts/backup.sh` — `pg_dump` the `throughline` database                      |
| `throughline-embed.timer`   | hourly at :15    | Rust `throughline migrate`, then `embed --limit 10000` — vectors for new notes first, then messages |

All four services are `Type=oneshot`: they run, finish, and exit. The timer units keep
them on schedule.

The ingest unit invokes the packaged `throughline ingest --all` command through
the shared environment. Its further steps run the Rust binary, the same one the
embed unit uses: `migrate`, then Gemini CLI, Copilot Chat, Markdown folders and
git repositories. Gemini CLI and Copilot Chat write nothing without their
folders. Markdown and git take `THROUGHLINE_MARKDOWN_ROOTS` and
`THROUGHLINE_GIT_ROOTS` from the shared file (distinct root folder names) and
write nothing while both are empty. Web pages are not on a timer:
`ingest --source web` makes network requests, so you start it yourself.

Every Rust job runs the Rust `migrate` before its work. The Python
`throughline migrate` does not add the columns and the `document` role the Rust
ingest and extraction need, and the Rust `ingest` and `extract-memory` stop with
``the database lacks migration …: run the Rust `throughline migrate` (rust/target/release/throughline migrate, or `docker compose run --rm migrate`)``
when they are missing. `migrate` does nothing on a current schema.

The document sources (Markdown folders, git repositories, web pages) and the
`document` extraction profile exist only in the Rust runtime. The Python
`throughline` CLI knows neither, and the Python extractor reads only user and
assistant messages, so it would leave every document unextracted. That is why
the extract unit runs the Rust binary too, with the model settings from the
shared file: `OLLAMA_HOST`, `OLLAMA_EMBED_MODEL`, and every model setting the
server sets, such as `THROUGHLINE_ANSWER_BACKEND`, `THROUGHLINE_ANSWER_BASE_URL`,
`THROUGHLINE_ANSWER_API_KEY`, `THROUGHLINE_ANSWER_MODEL`,
`THROUGHLINE_OLLAMA_THINK`, `THROUGHLINE_OLLAMA_NUM_CTX`,
`THROUGHLINE_LLM_TIMEOUT_SECS`, `THROUGHLINE_EMBED_MODEL` and
`THROUGHLINE_EXTRACT_PROFILE`. Without them it falls back to the local Ollama
defaults.

The backup unit retains a source-checkout compatibility wrapper; installed and
interactive use should prefer `throughline backup`.

The embed unit needs the Rust runtime: it runs
`~/.local/share/throughline/rust/target/release/throughline`, which
`cargo build --release --manifest-path rust/Cargo.toml -p throughline` builds in
the checkout. The Python CLI's `embed` ignores `OLLAMA_EMBED_MODEL` and uses
`nomic-embed-text` on Ollama, so after a switch of the embedding model it fills
nothing recall reads. Recall
ranks by meaning (cosine) by default, so a note needs a vector of the model the
server uses. A new note gets one when it is written; the embed job fills in what
that misses, such as notes written while Ollama was down. With the Compose stack,
replace its `ExecStart` with the commented `docker compose exec -T web` line in
the unit, which takes the model from `.env`.

## Prerequisites

- Linux with `systemd --user` support (any mainstream distro from the last five years).
- Throughline installed for the systemd user, with its console script on
  `~/.local/bin` or another path available to `/usr/bin/env`.
- A running PostgreSQL with the `throughline` database. Run `throughline migrate`
  once after creating a native database; Docker Compose does this automatically.
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

# 3) Install one shared database configuration for all four jobs.
mkdir -p ~/.config/throughline
install -m 600 systemd/throughline.env ~/.config/throughline/throughline.env

# 4) Tell systemd about the new units.
systemctl --user daemon-reload

# 5) Enable + start the four timers.
systemctl --user enable --now \
    throughline-ingest.timer \
    throughline-extract.timer \
    throughline-backup.timer \
    throughline-embed.timer
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

All four services read `~/.config/throughline/throughline.env`, which owns
`PGDATABASE`, `PGHOST`, and `PGPORT`, for the embed and extract jobs
`OLLAMA_HOST` and `OLLAMA_EMBED_MODEL`, and for the ingest job the two lists
`THROUGHLINE_MARKDOWN_ROOTS` and `THROUGHLINE_GIT_ROOTS`. Set both to exactly the values the server uses: vectors
are keyed by the model string, and an empty `OLLAMA_EMBED_MODEL` means the
default `nomic-embed-text`. `PGUSER` remains the systemd user. Add
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
extract, backup and embed jobs then use it:

```ini
PGDATABASE=claude_memory
```

The packaged backup resolves `pg_dump` from `PATH` on Linux. Set `PG_DUMP_BIN`
to a full executable path, or `PG_BIN` to a directory containing PostgreSQL
tools, when a distribution keeps them outside the service `PATH`.

## Uninstall

```bash
systemctl --user disable --now \
    throughline-ingest.timer \
    throughline-extract.timer \
    throughline-backup.timer \
    throughline-embed.timer
rm ~/.config/systemd/user/throughline-*.{service,timer}
systemctl --user daemon-reload
```
