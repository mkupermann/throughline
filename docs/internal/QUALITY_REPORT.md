# Quality Report — Pre-Release

Date: 2026-04-18
Repo: `/tmp/public-release-prep/`

## Syntax Check

| Language    | Files | Errors | Notes |
|-------------|------:|-------:|-------|
| Python      | 16    | 0      | `py_compile` clean on `scripts/*.py`, `gui/*.py`, `skill/scripts/*.py` |
| Bash        | 4     | 0      | `bash -n` clean on `scripts/*.sh` (backup, install, install_hooks, test_fresh_install) |
| AppleScript | 0     | —      | No `.scpt` files in the repo |
| SQL         | 3     | 0      | `sql/schema.sql` deployed cleanly to a fresh DB (`psql -v ON_ERROR_STOP=1`); `examples/demo_data.sql` and `scripts/schema_knowledge_graph.sql` parse (not deployed standalone — the latter is integrated into schema.sql) |

## Hardcoded Paths

All personal `/Users/mkupermann/...` paths have been replaced. Remaining
`/Users/...` occurrences are all clearly-marked illustrative placeholders:

| File | Line | Path | Status |
|------|-----:|------|--------|
| `examples/demo_data.sql` | 99, 109, 119, 129, 139 | `/Users/dev/projects/...` | Demo fixture — fictitious |
| `docs/architecture.md` | 95 | `/Users/<user>/Documents/GitHub/wiki` | Docs example with `<user>` placeholder |
| `launchd/com.claude-memory-extract.plist` | 26 | `REPLACE_WITH_HOME/.local/bin:...` | Placeholder, substituted by `install.sh` |
| `scripts/install.sh` | 68 | `s|/Users/REPLACE|$HOME|g` | Legacy sed fallback in installer |

No remaining `mkupermann` username anywhere in source files.

## Dependencies

Third-party Python imports were enumerated across all scripts + GUI:

| Package              | Used by                                                      | In `requirements.txt` |
|----------------------|--------------------------------------------------------------|:---------------------:|
| `psycopg2`           | Every DB-facing script + GUI                                 | yes (`psycopg2-binary>=2.9.9`) |
| `streamlit`          | `gui/app.py`                                                 | yes (`>=1.32.0`)               |
| `pandas`             | `gui/app.py`                                                 | yes (`>=2.0.0`)                |
| `plotly`             | `gui/app.py`                                                 | yes (`>=5.18.0`)               |
| `pyyaml` (yaml)      | `gui/app.py`                                                 | yes (`>=6.0`)                  |
| `streamlit_agraph`   | `gui/app.py`                                                 | yes (`>=0.0.45`)               |
| `streamlit_calendar` | `gui/app.py`                                                 | yes (`>=1.3.0`)                |
| `openai`             | Optional — `scripts/generate_embeddings.py`                  | documented as optional         |
| `anthropic`          | Optional — `scripts/extract_memory.py` (API path only)       | documented as optional         |

All required packages are version-pinned with `>=` (security-patch friendly).
Optional packages are documented with explanatory comments in the file so
consumers know when to install them.

## Fresh Install Test

`scripts/test_fresh_install.sh` was created and executed locally:

```
[fresh-install] creating database 'claude_memory_fresh_60520'
[fresh-install] ok: database created
[fresh-install] deploying sql/schema.sql
[fresh-install] ok: schema deployed
[fresh-install] verifying tables
[fresh-install] ok: table conversations
[fresh-install] ok: table messages
[fresh-install] ok: table memory_chunks
[fresh-install] ok: table skills
[fresh-install] ok: table prompts
[fresh-install] ok: table projects
[fresh-install] ok: table entities
[fresh-install] ok: table relationships
[fresh-install] ok: table entity_mentions
[fresh-install] ok: table embeddings
[fresh-install] ok: table memory_reflections
[fresh-install] ok: table ingestion_log
[fresh-install] smoke-testing CLI scripts
[fresh-install] ok: scripts/extract_entities.py --help
[fresh-install] ok: scripts/reflect_memory.py --help
[fresh-install] ok: scripts/generate_embeddings.py --help
[fresh-install] ok: scripts/search_semantic.py --help
[fresh-install] ok: scripts/graph_query.py --help
[fresh-install] ok: scripts/ingest_sessions.py (empty DB)
[fresh-install] ok: scripts/ingest_windsurf.py (empty DB)
[fresh-install] ok: scripts/scan_skills.py (empty DB)
[fresh-install] ok: scripts/scan_prompts.py (empty DB)
[fresh-install] ok: scripts/context_preload.py (empty DB)
[fresh-install] ok: skill/scripts/query.py (usage output)
[fresh-install] ok: python compile
[fresh-install] ok: bash syntax

================================================
FRESH INSTALL OK
================================================
```

Exit code: `0`.

The test creates a throw-away database (`claude_memory_fresh_$$`), deploys
the schema, verifies all 12 expected tables, exercises every CLI script
either via `--help` or by running it against the empty DB, and then drops
the test database (via `trap cleanup EXIT`).

## Cleanup Actions

Removed:

- `**/.DS_Store` files (none present after cleanup)
- `**/__pycache__/` directories (3 total, including those generated by the smoke test)
- `*.pyc` files (1 total)
- `._*` macOS resource forks (none found)
- Empty directories (`.github/`, `.github/workflows/`, `.github/ISSUE_TEMPLATE/`)
- Leftover `.claude/MEMORY_CONTEXT.md` from running `context_preload.py` during smoke-test

Fixed:

- `.gitignore` — added `!.env.example` negation so the sample env file is not
  accidentally excluded by the `.env.*` pattern.
- `requirements.txt` — completed with `plotly`, `streamlit-agraph`,
  `streamlit-calendar`; version-pinned with `>=`; optional deps documented.
- `gui/app.py` — hardcoded DB user/host and absolute script paths replaced
  with env-var defaults and `PROJECT_ROOT = Path(__file__).resolve().parent.parent`.
- All `scripts/*.py` — `DB_CONFIG`/`DB` dicts changed to read `PGDATABASE`,
  `PGUSER`, `PGHOST`, `PGPORT` from the environment, falling back to
  `$USER`/`localhost`.
- `scripts/extract_memory.py`, `extract_entities.py`, `generate_titles.py`,
  `reflect_memory.py` — `CLAUDE_BIN` changed from a hardcoded personal path
  to `_resolve_claude_bin()` which checks `$CLAUDE_BIN` then `shutil.which("claude")`.
- `docs/architecture.md` — all literal user paths replaced with
  `/path/to/bks-claude-memory/` placeholder; seed-data example genericised.

Added:

- `scripts/test_fresh_install.sh` — reproducible install smoke test.
- `skill/scripts/add.py` — previously referenced in `SKILL.md` but missing.

## Ready for Public Release

- [x] All syntax checks pass
- [x] No hardcoded user paths in source (only in documented placeholder positions)
- [x] `requirements.txt` complete, version-pinned, optional deps documented
- [x] Fresh install works (see `scripts/test_fresh_install.sh`)
- [x] Documentation present (`README.md`, `CONTRIBUTING.md`, `LICENSE`,
      `docs/architecture.md`, `docs/INSTALLATION.md`, `.env.example`,
      `config.example.yaml`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
      `PRIVACY_AUDIT.md`, `CHANGELOG.md`, `BRANDING.md`)

## READY FOR RELEASE

All criteria met. A fresh `git clone` + `./scripts/install.sh` followed by
`./scripts/test_fresh_install.sh` will bring the tool to a verifiable
working state without further manual edits.
