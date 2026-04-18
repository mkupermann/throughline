# Security Policy

## Threat Model

This tool is a **local-first, single-user** memory database. It is not designed
to be exposed on a network or shared between users. That context shapes the
security model.

### In scope

- PostgreSQL database running on `localhost:5432`
- Python scripts reading/writing local files under `~/.claude/` and the
  repository directory
- Streamlit GUI on `http://localhost:8501`
- launchd jobs that run as the logged-in user
- API keys (optional) for OpenAI or Anthropic, stored in environment variables

### Out of scope

- Multi-user deployments (the default `trust` auth is single-user only)
- Network-exposed databases or UIs — running Streamlit on `0.0.0.0` is not
  supported and not recommended
- Shared CI/CD infrastructure

## Known Considerations

### Database access

The default installation uses PostgreSQL's `trust` authentication — anyone with
shell access to your machine can read the `claude_memory` database. If your
machine is shared, switch to password auth or `scram-sha-256` and set
`PGPASSWORD` in your environment.

### Session data is sensitive

Claude Code JSONL sessions may contain:

- File paths that reveal proprietary code structure
- Snippets of source code, config values, or prompts
- Tool-call arguments that may include paths or identifiers
- Email addresses, user names, and project names mentioned in conversations

Treat the `claude_memory` database as confidential by default. Do not commit
database dumps, do not share backups, do not upload to cloud storage without
encryption.

### API keys

Scripts read `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from the environment.
These must never be committed. `.env` files are gitignored — verify with
`git check-ignore -v .env` before any commit.

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
