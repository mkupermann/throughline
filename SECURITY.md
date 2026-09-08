# Security Policy

## Threat Model

This tool is a **local-first, single-user** memory database. It is not designed
to be exposed on a network or shared between users. That context shapes the
security model.

### In scope

- PostgreSQL database running on a local native port (normally
  `localhost:5432`) or the Compose loopback port (normally `127.0.0.1:5433`)
- Throughline reading local session files for the supported source tools and
  writing its own database and configuration
- web UI on `http://127.0.0.1:8790` (native) or `:8788` (Docker)
- launchd or systemd user jobs that run as the local user
- Optional provider API keys in the local database or environment variables, and host CLI credentials

### Out of scope

- Multi-user deployments
- Network-exposed databases or UIs. A native `throughline serve` refuses a
  non-loopback bind unless `THROUGHLINE_ALLOW_REMOTE=1` is set. That bypass is
  only for an operator who has added their own authentication and TLS.
- Shared CI/CD infrastructure

## Known Considerations

### Where stored content can leave the machine

The selected model or service receives data for these optional operations:

1. **Answers** send retrieved excerpts and the question.
2. **Project names, conversation titles, knowledge, entities and reflection** send source previews or candidate knowledge records. Extraction redaction is on by default; `THROUGHLINE_REDACT_PII=0` disables it. This does not imply that every other purpose uses the same redaction path.
3. **Embeddings** send text to the chosen embedding API.

Saved **AI settings** bindings take precedence over legacy environment defaults and the embedding CLI's `--backend` flag. They never silently fall back to another provider. For local-only processing, save local endpoints for every purpose. Without a saved binding, embedding `auto` uses hosted OpenAI when `OPENAI_API_KEY` is set; generation uses its existing auto-detection path. A loopback client or a locally installed CLI does not make a hosted model local.

The optional host bridge executes only its registered Codex, Vibe and Claude adapters, in temporary directories with bounded concurrency and a timeout. It requires a bearer token. CLI authentication stays on the host. Requests can still be sent remotely through the CLI's configured service. Keep the bridge on a trusted host/container network; never publish its token. A local Stop cannot retract an already submitted provider request. See [AI processing](docs/AI_PROCESSING.md).

Connection tests send synthetic content, not source conversations. Provider/settings API responses omit API keys, but the local database stores provider keys as plaintext; the read-only SQL Console and database backups can expose them. Treat access to the local app as access to those credentials.

Retrieval, ranking, indexing, embeddings against a local backend, and every
listing in the UI are entirely local. There is no telemetry and no account.

### The API has no authentication

`throughline serve` binds to loopback and Compose publishes PostgreSQL, the web
UI, and optional Ollama on loopback only. There is no login. Anything that can
reach the web port can read the stored corpus, and the Console endpoint accepts
arbitrary read-only SQL. Do not expose these ports, tunnel them, or use the
remote-bind bypass unless you operate suitable authentication and TLS in front
of them. Treat shell access to the machine as full access to the database.

Compose deliberately lets the web container bind internally so Docker can
publish its port. The host mapping remains `127.0.0.1`, and only that controlled
service receives `THROUGHLINE_ALLOW_REMOTE=1`.

### The Markdown export is the one endpoint that writes files

Every other endpoint reads, or runs a job whose command line is fixed. The
Markdown export takes a destination from the caller, which on an
unauthenticated API is a different kind of capability — so it is bounded on
both sides:

- The destination must be an absolute path inside `THROUGHLINE_EXPORT_ROOT`
  (the user's home directory by default). Symlinks are resolved before the
  containment check, and a path naming an existing file is refused.
- The destination reaches the job through the environment, never as a
  command-line argument. The job registry's guarantee — that no request body
  becomes argv — is not spent on this feature.

Narrow the root if the service shares a machine with anything you would rather
it could not write into:

```bash
THROUGHLINE_EXPORT_ROOT=~/Exports throughline serve
```

An export contains the transcripts themselves. Writing one into a synced or
shared folder puts that content wherever the folder goes; `--redact` runs every
exported text through [`throughline/pii.py`](throughline/pii.py) first, with
the same conservative-by-design caveat as the extraction pass below.

### Encryption at rest

There is none, beyond whatever the disk provides. The database is a normal
PostgreSQL cluster and the backups are plain `pg_dump` output. On macOS,
FileVault covers both; on Linux, use an encrypted volume if the corpus warrants
it.

### Database access

Native PostgreSQL authentication is the operator's choice. Compose requires
`POSTGRES_PASSWORD`; `scripts/init_compose_env.py` creates an owner-only `.env`
with a random password and the host UID/GID. Application containers run as an
unprivileged `throughline` user and mount source directories read-only. Re-run
the bootstrap script after moving a checkout between host users, then rebuild.

On an existing Compose volume, `POSTGRES_USER` and `POSTGRES_DB` are immutable
identities. Changing a password needs the documented `credential-rotate`
profile; it does not rename a database or role. Keep `.env` and backups private.

### Session data is sensitive

AI-tool session files may contain:

- File paths that reveal proprietary code structure
- Snippets of source code, config values, or prompts
- Tool-call arguments that may include paths or identifiers
- Email addresses, user names, and project names mentioned in conversations

Treat the Throughline database as confidential by default. Do not commit
database dumps, do not share backups, do not upload to cloud storage without
encryption.

### PII / secret redaction before extraction

Before each conversation transcript is sent to the extraction model,
it runs through a heuristic redaction pass in [`throughline/pii.py`](throughline/pii.py).
The pass replaces recognisable Anthropic / OpenAI / GitHub / AWS / Google /
Slack / Stripe API-key shapes, JWTs, `Authorization: Bearer` headers, explicit
`password=` / `secret=` / `token=` assignments, private-key blocks, email
addresses, and home-directory usernames in file paths.

Conservative by design — we prefer leaking an uncommon secret shape to
destroying legitimate memory content. Override with the environment variable
`THROUGHLINE_REDACT_PII=0` if you are processing synthetic data and want the
raw transcript to reach the model.

### API keys

Throughline reads `OPENAI_API_KEY` from the environment. It must never be
committed. `.env` files are gitignored — verify with
`git check-ignore -v .env` before any commit.

Provider keys entered through AI-team operations are stored as plaintext in
`pm_ai_providers.api_key`. Provider/settings responses omit them, but the read-only SQL Console, database access
and backups can expose the underlying values. Protect the database and backup
files as credentials. Encrypted or external secret storage is not implemented.

### Acceptable use

Whichever model you point Throughline at, its provider's terms still apply.
Either way you are responsible for not feeding a hosted model content you are
not permitted to send it.

### Backups

The backup script writes `pg_dump` output to
`~/.local/share/claude-memory/backups/` by default. Compose instead stores
owner-only dumps in the persistent named volume `throughline_backup_data`.
These files are unencrypted. If you back them up to cloud storage, encrypt them first
(for example, with `age` or `gpg`).

### AppleScript automation

The optional macOS hooks use AppleScript to talk to Mail, Calendar, and
Finder. These automations require TCC (Transparency, Consent, Control)
permissions and can read/write to those apps. Grant access only if you
trust the scripts — they are all visible in `scripts/` and `skill/scripts/`.

## Reporting a Vulnerability

If you discover a vulnerability — something that lets an attacker read,
modify, or delete data outside of the intended single-user local scope —
please report it responsibly.

1. Open a **private security advisory** on GitHub:
   `Security → Advisories → Report a vulnerability`
2. Include a minimal reproduction and your environment (OS, Python,
   PostgreSQL versions).
3. Expect an acknowledgment within 7 days.

Do **not** file public issues for security bugs. Public disclosure before
a fix puts every user at risk.

## Supported Versions

Only the `main` branch receives security fixes. If a released version is
marked in the CHANGELOG, the most recent tag is also supported for 90 days
after release.
