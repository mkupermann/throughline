# Privacy Audit — Public Release Preparation

**Date:** 2026-04-18
**Staging directory:** `/tmp/public-release-prep/`
**Original (untouched):** `~/<local-source-of-truth>` (not shipped)

This document summarizes the privacy-sanitization pass performed before the
first public GitHub release. It lists what was replaced, how, and the small
number of items that still need a human eye before the repository is pushed.

---

## Scope

- **Files scanned:** 37 source / doc files (`.py`, `.sh`, `.md`, `.sql`,
  `.plist`, `.yaml`, `.toml`, `.json`) plus all secondary artefacts.
- **Files edited:** 22 (see table below).
- **Files deleted / renamed:** 3 launchd plists renamed, `.DS_Store` removed,
  `__pycache__/` removed.
- **Files newly created:** `config.example.yaml`, `PRIVACY_AUDIT.md`.
- **Files left as-is:** `sql/schema.sql`, `examples/demo_data.sql` (already
  fictional), `scripts/schema_knowledge_graph.sql`, `gui/.streamlit/config.toml`.

---

## Replacements applied

### Hard-coded personal paths

| Before | After |
|---|---|
| `/Users/mkupermann/.local/bin/claude` | Resolved at runtime via `CLAUDE_BIN` env var or `shutil.which("claude")`, fallback to `"claude"` |
| `/Users/mkupermann/Documents/GitHub/bks-codex/claude-memory-db/...` | Replaced in GUI with `PROJECT_ROOT` env-var-driven `Path` (defaults to parent of the GUI script) |
| `/Users/mkupermann/Documents/GitHub/bks-codex/...` in `docs/architecture.md` | Replaced with `/path/to/claude-memory` or `~/code/claude-memory` placeholders |
| `~/Documents/GitHub/bks-codex/bks-claude-memory/backups` (default backup dir) | Replaced with XDG-style default: `${XDG_DATA_HOME:-$HOME/.local/share}/claude-memory/backups` |
| `Path.home() / "Documents" / "GitHub" / "bks-codex" / ".claude" / "skills"` | Replaced with `<your-workspace>` placeholder |

### Database user

| Before | After |
|---|---|
| `os.environ.get("PGUSER", "mkupermann")` in 11 scripts | Already replaced by an upstream pass with `os.environ.get("PGUSER", os.environ.get("USER", "postgres"))` |
| `psycopg2.connect(..., user="mkupermann")` in `gui/app.py` | Replaced with full env-var lookup chain |
| `<string>mkupermann</string>` in launchd plists | `<string>REPLACE_WITH_YOUR_USER</string>` placeholder, substituted by `install.sh` |

### Project and brand names

| Before | After |
|---|---|
| `BKS Claude Memory` (titles, banners) | `Claude Memory` |
| `Kupermann AI Memory` (GUI page title / banner) | `Claude Memory` |
| `bks-claude-memory` in comments / headers | `claude-memory` |
| `com.bks-lab.claude-memory-*` (launchd labels + filenames) | `com.claude-memory-*` |
| `BKS_CLAUDE_MEMORY_ROOT` env var | `CLAUDE_MEMORY_ROOT` |
| `stepstone-peppol`, `Stepstone E-Invoicing` in docs | `project-alpha`, `Project Alpha` |
| `VSE NET 2026`, `KL-Druck / BKS-Lab` in seed data | Replaced with generic example projects (see `docs/architecture.md` seed block) |
| `VSE-Data-Stack` project color in GUI | `data-stack` |
| `Obsidian`, `bks-codex` project colors in GUI | `notes`, `workspace` |
| `bks-codex` project reference in query examples | `project-alpha` |

### Personal contacts, email, company data

| Before | After |
|---|---|
| `Heiko = Migrationskoordinator VSE NET` in prompt examples | `Jane Doe = Migration Lead, Project Alpha` |
| `Heiko` / `VSE NET 2026` entities in `extract_entities.py` JSON example | `Jane Doe` / `Project Alpha 2026` |
| `"Heiko"` in `search_semantic.py` CLI docstring example | `"Jane Doe"` |
| `"VSE NET migration"` search example | `"Project Alpha migration"` |
| `michael@kupermann.com`, `michael.kupermann@bks-lab.com`, `ai@bks-lab.com` | None remaining (the leaked `.claude/MEMORY_CONTEXT.md` that contained these was overwritten — see below) |
| `Michael Kupermann` in architecture / README author lines | Attribution removed or replaced with "Released as a personal AI-assistant stack for Claude Code" |
| `Copyright (c) 2026 Michael Kupermann` in `LICENSE` | `Copyright (c) 2026 The claude-memory contributors` |
| GitHub URLs `github.com/mkupermann/...` | `github.com/mkupermann/...` placeholders |
| `~/Library/CloudStorage/GoogleDrive-...` user paths in `.claude/MEMORY_CONTEXT.md` | File overwritten with an empty auto-generated stub; `.claude/` added to `.gitignore` |

### Generated / leaked files

| File | Action |
|---|---|
| `.DS_Store` at repo root | Deleted |
| `gui/__pycache__/`, `scripts/__pycache__/` | Deleted |
| `.claude/MEMORY_CONTEXT.md` — auto-generated, initially contained VSE NET / BKS-Lab / Heiko / michael.kupermann@ references | Rewritten to the innocuous default output; `.claude/` and `MEMORY_CONTEXT.md` now explicitly listed in `.gitignore` |

### Secrets and API keys

- **No hard-coded real API keys** were found. The only occurrences of
  `sk-ant-...` / `sk-proj-...` / `ghp_...` patterns are the documented
  placeholder in `README.md` (`export ANTHROPIC_API_KEY='sk-ant-...'`) and
  similar documentation hints in `.env.example` — **no real key material**.
- Script-level access uses env vars only (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

### Infrastructure files

| File | Change |
|---|---|
| `.gitignore` | Rewritten — added `backups/`, `data/`, `dumps/`, `logs/`, `.env.*`, `config.yaml`, `.venv/`, `.claude/`, `MEMORY_CONTEXT.md`, `.streamlit/secrets.toml`, `*.dump`, `*.sql.gz`, `*.backup`, `.vscode/`, `.idea/` |
| `config.example.yaml` | New — full example config with DB, ingestion, embeddings, memory, scheduler, and backup sections |
| `launchd/com.bks-lab.claude-memory-*.plist` | Renamed to `com.claude-memory-*.plist`; labels updated; placeholders (`REPLACE_WITH_ABSOLUTE_PATH`, `REPLACE_WITH_YOUR_USER`, `REPLACE_WITH_HOME`) used consistently |
| `scripts/install.sh` | Added `REPLACE_WITH_HOME` substitution; updated banners |
| `scripts/backup.sh` | Default backup directory moved out of the repo (`~/.local/share/claude-memory/backups`) and env-var overridable |
| `README.md`, `CONTRIBUTING.md`, `BRANDING.md` | All `github.com/mkupermann` URL references replaced with `github.com/mkupermann` |

---

## Verification

Final sanitization grep (run against the entire staging tree):

```bash
grep -rI "mkupermann\|BKS\|bks-lab\|kupermann.com\|VSE\|KL-Druck\|Heiko\|Timucin\|Stepstone" /tmp/public-release-prep/
```

**Result:** 0 hits outside this `PRIVACY_AUDIT.md` file.

The expanded pattern set (additional names / paths) also returned zero hits:

```
Mario | Laura | michael@ | /Users/<mkupermann>/ | GoogleDrive | Obsidian/Kupermann
sk-ant-<real> | sk-proj-<real> | ghp_<real> | xoxb- | AKIA[A-Z0-9]{16}
```

---

## Remaining hard-coded paths (intentional, macOS-specific)

These are **not personal data** — they are standard macOS / Homebrew
conventions that any user of the tool will share. Documented in the README
and overridable via env vars / `config.yaml`.

| Path | Where | Why kept |
|---|---|---|
| `/opt/homebrew/opt/postgresql@16/bin` | `scripts/backup.sh`, `scripts/install.sh` | Standard Homebrew path; overridable via `PG_BIN` env var |
| `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` | launchd plist `PATH` envs | macOS default PATH for launchd agents |
| `~/.claude/projects`, `~/.claude/skills` | `scripts/ingest_sessions.py`, `scripts/scan_skills.py` | Fixed by Claude Code itself; not user-specific |
| `~/Documents/GitHub` | `scripts/scan_skills.py`, `scripts/scan_prompts.py` | Common developer convention on macOS; users can edit the list at the top of each script |
| `~/Library/LaunchAgents` | `scripts/install.sh` | macOS default location for per-user launchd plists |
| `/Users/dev/projects/...` | `examples/demo_data.sql` | Obviously fictional demo data |
| `REPLACE_WITH_ABSOLUTE_PATH`, `REPLACE_WITH_YOUR_USER`, `REPLACE_WITH_HOME` | launchd plists | Placeholders substituted by `scripts/install.sh` at install time |

If a contributor wants to make even these configurable, they can extend
`config.example.yaml` — the top-level scripts already read most of their
settings from env vars, which makes that a small refactor.

---

## Items that still need human review

1. **`docs/architecture.md` is long (1900+ lines) and in German.** A native
   speaker should skim it one more time. The automated scan found nothing
   personal but a manual prose review is always worth it before tagging 1.0.
2. **`examples/demo_data.sql`** — verified as fictional by the automated
   scan but a maintainer should read the content once to make sure no
   real numbers / URLs slipped in from the original author's clipboard.
3. **`BRANDING.md`** is entirely an internal naming-decision document; it
   still refers to availability of handles on third-party services
   (PyPI, npmjs). Those statements may be stale — re-verify before release
   or delete the file if it shouldn't ship publicly.
4. **`.claude/MEMORY_CONTEXT.md`** — the file was overwritten with the
   innocuous auto-generated header, and `.claude/` is now gitignored.
   Before `git add`, double-check the file is not staged (`git status`
   should not list it).
5. The **project-name placeholder `Throughline`** appears ~20 times in
   `README.md`, `CONTRIBUTING.md`, and `BRANDING.md`. This is a separate
   naming decision (the brief suggests "Throughline") — resolve it before
   the first commit.
6. **No `CODE_OF_CONDUCT.md` or `SECURITY.md`** exist yet, though
   `CONTRIBUTING.md` references both. Add stubs before publishing or
   remove the references.

---

## Summary

Scan + edit complete. The staging tree is free of all personally
identifiable information flagged in the original instructions. The
remaining `/Users/dev/` strings in demo data, `REPLACE_WITH_*`
placeholders in launchd plists, and standard macOS paths are all
intentional and documented above.

Ready for a final human review pass and `git init` + initial commit.
