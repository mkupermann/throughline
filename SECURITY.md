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
- API keys (optional) for OpenAI or Anthropic, stored in environment variables

### Out of scope

- Multi-user deployments
- Network-exposed databases or UIs. A native `throughline serve` refuses a
  non-loopback bind unless `THROUGHLINE_ALLOW_REMOTE=1` is set. That bypass is
  only for an operator who has added their own authentication and TLS.
- Shared CI/CD infrastructure

## Known Considerations

### Where stored content can leave the machine

Three places, all of them a model call:

1. **Answering a question.** `throughline ask`, and the Ask panel in the UI,
   send the retrieved excerpts to whichever model answers. With a local backend
   (Ollama, LM Studio, llama.cpp, vLLM) the prompt never leaves the machine, and
   `auto` probes local backends first for exactly this reason.
   `throughline doctor` prints which model will answer and whether it is local;
   the UI states it with every answer. `THROUGHLINE_REDACT_PROMPTS=1` runs the
   excerpts through [`throughline/pii.py`](throughline/pii.py) first — off by
   default, because this is your own history on your own machine.
2. **Memory extraction, title generation and reflection.** These send
   transcripts or chunk pairs to the same probed backend, local first.
   Redaction is **on** by default for extraction;
   `THROUGHLINE_REDACT_PII=0` disables it. All three are optional — skip them
   and the rest of the tool still works.
3. **Embeddings.** `throughline embed` uses Ollama by default and never leaves
   the machine; it reaches the network only if you select the OpenAI backend.

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

Throughline reads `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from the environment.
These must never be committed. `.env` files are gitignored — verify with
`git check-ignore -v .env` before any commit.

### Acceptable use

Whichever model you point Throughline at, its provider's terms still apply —
Anthropic's [Usage Policy](https://www.anthropic.com/legal/aup) for Claude,
and the equivalent for OpenAI, Mistral or anyone else. A local model has no
such terms, which is one more reason the probe order prefers it. Either way you
are responsible for not feeding a hosted extractor content you are not
permitted to send it.

### Backups

The backup script writes `pg_dump` output to
`~/.local/share/claude-memory/backups/` by default. These files are
unencrypted. If you back them up to cloud storage, encrypt them first
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
