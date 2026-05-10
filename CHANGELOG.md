# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Project detail tabs now find conversations / skills / prompts whose
  paths contain a hyphenated repo name.** Root cause:
  `scripts/ingest_sessions.py` derives `conversations.project_path` from
  Claude Code's session-hash by replacing every `-` with `/`, so a real
  repo `claude-memory-db` ends up stored as `…/claude/memory/db/…` and
  the literal string never appears anywhere in the table. The Project
  detail tabs now match against both the literal project name AND its
  hyphens-to-slashes variant on `project_path` / `path` / `source_path`,
  so `claude-memory-db` now lists the 2,000+ conversations it actually
  has instead of zero. The underlying ingest bug is tracked separately;
  this UI logic is forwards-compatible with a future repair.

### Added

- **GUI Project detail page — related-artifact tabs.** Opening a project
  now shows seven tabs: Overview (the existing edit form), Memory,
  Conversations, Entities, Skills, Prompts, Reflections. Each tab is
  scoped to the project's `name` — memory chunks / conversations /
  entities by `project_name`, skills/prompts by path-component match
  (`…/{name}/.claude/skills/…`), reflections by joining
  `memory_reflections.affected_chunks` to chunks belonging to the
  project. Each row is click-through to the artifact's own detail page,
  with CSV/Excel/PDF export available per tab. Closes the gap where
  "open this project" only let you edit a record but said nothing about
  what was inside it.
- **GUI Projects page — sort, list view, synthesised descriptions.** The
  page now has a Sort selector (Recent activity / Created / Name /
  Memory volume / Status) and a Cards|List view toggle. Each row joins
  live activity stats from `memory_chunks` + `conversations` so projects
  with empty `description` fields render a synthesised blurb (e.g.
  *"42 chunks · 6 conversations · last active 2 weeks ago"*) instead of
  a static "No description". Sorting by Recent activity makes the page
  useful immediately after `throughline backfill-projects`, when every
  curated row has the same `created_at`.
- **`throughline backfill-projects` subcommand.** Populates the `projects`
  table from distinct `project_name` values observed in `memory_chunks`
  (and optionally `conversations`). Closes the gap where a fresh-ingested
  DB referenced dozens of projects in chunks/conversations but the GUI
  Projects page was empty because that table was only ever written by the
  manual "New project" form. Idempotent (`ON CONFLICT (name) DO NOTHING`)
  — re-running never clobbers manually-curated descriptions, contacts,
  decisions or status. `--dry-run` previews; `--include-conversations`
  widens the source net.
- **`throughline status` subcommand.** Health snapshot of the memory DB:
  reachability, schema version (best-effort), table row counts,
  memory-chunk totals + by category, embedding coverage %, last-extraction
  / last-reflection timestamps, contradictions outstanding, project
  count. Plain text by default; `--json [--pretty]` for machine consumers
  (Docker healthcheck, monitoring scrapes, fresh-clone smoke). When the DB
  is unreachable the JSON payload still parses, with `db_reachable=false`.
- **`memory.stats` MCP tool.** Exposes the same payload as
  `throughline status --json` over MCP, so an agent can ask "what's
  actually in memory?" before a long retrieval session or a reflection
  pass. One helper (`throughline.status.collect_status`) backs the CLI,
  the MCP tool, and the GUI Memory Health card — three surfaces, one
  source of truth.
- **GUI Memory Health card.** Four KPI tiles below the existing Dashboard
  metric row (embedding coverage, projects, contradictions outstanding,
  last reflection). Hides itself silently if the DB is unreachable, so
  intermittent-DB demo deploys keep working.
- **Eval harness — `--offline-stub` and DB-free `--dry-run`.**
  `--dry-run` no longer needs a DB or API key — it parses the questions
  and exits 0. New `--offline-stub` runs the harness end-to-end with a
  deterministic pretend-LLM (always 30/30 with-memory vs 0/30 cold), so
  CI can prove the grader, retrieval glue, and report writer are intact
  without spending tokens. Real eval runs use neither flag and call
  Claude as before.
- **CI `eval-smoke` job** (`.github/workflows/ci.yml`). Runs
  `throughline status --json` against an unreachable Postgres and asserts
  the payload still parses, then runs both eval modes and asserts the
  Markdown report has the headline line. Catches the regression class
  where the harness or status payload starts requiring a live DB or API
  key — the failure mode that breaks fresh forks.
- **Preload audit row.** `scripts/context_preload.py` now writes a
  `memory_reflections` row with `reflection_type='preload'` whenever the
  SessionStart hook fires. The row records the chunk IDs that were injected
  into `MEMORY_CONTEXT.md` and the project name, so users (and the agent
  itself) can answer "what did I see at session start today?".
- **Two MCP tools** for visibility into the audit log:
  - `memory.recent_reflections(limit, types)` — recent rows from the
    `memory_reflections` audit log, optionally filtered by reflection_type.
  - `memory.preload_summary()` — the most recent `'preload'` audit row,
    so the agent can reason about what context it was given.
- **`THROUGHLINE_PROJECT_SCOPE_STRICT` env var.** When set, the MCP server
  refuses the `project=""` cross-project opt-out — every call must specify
  a project name. Enforces data isolation between client engagements at
  policy level rather than convention.
- **GUI-side PII redaction.** The Streamlit conversation viewer now pipes
  raw message bodies through `throughline.pii.redact` before rendering, so
  secrets that scrolled past in a Bash output stay out of the UI. Toggle in
  the sidebar (`Redact secrets in views`); default ON.
- **`evals/` harness** (scaffolded). 30-question starter set
  (`evals/questions.jsonl`) + a runner (`evals/run_eval.py`) that asks
  Claude each question with vs without retrieved memory, scores answers by
  human-authored substring match, and writes a Markdown report. Method
  documented in `evals/README.md`. Not yet run — the framework is in place,
  the numbers are TBD.
- **GHCR release workflow** (`.github/workflows/release.yml`). On every
  version tag, builds a multi-arch Docker image and pushes it to
  `ghcr.io/mkupermann/throughline:{version}`, so users can run the GUI
  with `docker run -p 8501:8501 ghcr.io/mkupermann/throughline:v0.2.1`
  without needing a local clone.

## [0.2.0] — 2026-04-29

### Added

- **`forget` primitive** (`scripts/forget.py`) — first-class cascade-delete
  for memory chunks and entities. `forget_chunks(ids, *, reason)` removes
  the rows AND their embeddings AND repairs dangling `superseded_by`
  references in one transaction, writing a `memory_reflections` row with
  `reflection_type='forget'`. `forget_entity(id, *, reason)` does the
  equivalent for entities (FK cascades through `entity_mentions` and
  `relationships`) and logs `'forget_entity'`. Wired into the GUI: Memory
  chunk detail, Knowledge Graph entity detail, and a Memory-page bulk
  forget expander (mandatory reason field).
- **MCP server** (`memory_mcp/`) — exposes six stdio tools so Claude Code
  (or any MCP client) can read and write its own long-term memory:
  `memory.search`, `memory.recall_entity` (BFS up to 3 hops with optional
  `relation_types` whitelist), `memory.write`, `memory.supersede` (with
  audit row), `memory.forget` (calls `scripts/forget.forget_chunks`), and
  `memory.list_projects`. Project scoping defaults to
  `basename($CLAUDE_PROJECT_DIR)`; pass `project=""` to opt out and search
  across projects. Package named `memory_mcp/` to avoid shadowing the
  official `mcp` SDK and the existing `throughline/` package.
- **Knowledge Graph keyword search** — text input filters the rendered
  graph by one or more keywords against entity names, with **Match all
  words** (AND vs default OR) and **Include neighbors** (1-hop expansion)
  toggles. Seed matches highlighted with larger nodes, accent-coloured
  labels and bold borders. Max-nodes ceiling raised 200 → 400.
- **Universal CSV / Excel / PDF export** — reusable
  `render_export_buttons()` helper drops three download buttons above
  every list view: Conversations, Memory, Memory Health (top-accessed +
  reflections + supersede/merge), Skills, Knowledge Graph entities,
  Projects, Prompts, every Global Search scope, and every Semantic Search
  scope. CSV is UTF-8 with BOM; Excel via `openpyxl`; PDF via `reportlab`
  (landscape A4, repeated header row, alternating row backgrounds, title
  + timestamp). Missing optional deps degrade gracefully — those buttons
  disappear and the page surfaces a `pip install` hint. CSV is always
  available.
- **PII / secret redaction** in `throughline/pii.py` that runs automatically
  before each transcript is sent to Claude for memory and entity extraction.
  Redacts Anthropic / OpenAI / GitHub / AWS / Google / Slack / Stripe
  API-key shapes, JWTs, bearer tokens, `password=` / `secret=` / `token=`
  assignments, private-key blocks, email addresses, and home-directory
  usernames. Default on. Disable with `THROUGHLINE_REDACT_PII=0` or
  `memory.redact_pii: false`. 18 unit tests in `tests/test_pii.py`.
- Linux systemd user-level service units and timers
  (`systemd/throughline-{ingest,extract,backup}.{service,timer}` + install guide in `systemd/README.md`).
- Schema migrations framework (`sql/migrations/` + `scripts/migrate.py`),
  with tracking table `applied_migrations` and the current schema captured as `000_baseline.sql`.
- Demo data loader (`scripts/load_demo.sh`) plus a new `demo` profile in
  `docker-compose.yml` that starts a throw-away Postgres on port 5433 with schema and demo data pre-loaded.
- Integration tests in `tests/integration/` (ingest, skill scan, memory query)
  backed by a per-test fresh-database fixture; runnable locally via `make test-integration`
  and on CI via the new `integration-tests` job (Postgres service).
- Pre-commit hooks configuration (`.pre-commit-config.yaml`) wiring up
  ruff, ruff-format, the fast unit-test subset, and standard sanity checks.
- `Makefile` targets `test-integration`, `migrate`, and `load-demo`; the
  default `test` target now runs unit tests only (no DB).
- `pyproject.toml` — installable via `pip install -e .` (PEP 621 metadata,
  optional extras: `openai`, `anthropic`, `dev`; configured `ruff` + `black`).
- Unified CLI: `throughline <command>` or `python -m throughline <command>`
  with subcommands for ingestion, scanning, extraction, embeddings,
  semantic search, reflection, GUI launch, hook installation, backup, and
  version reporting. Each subcommand is a thin wrapper around the
  corresponding script in `scripts/` so direct execution keeps working.
- `Makefile` shortcut targets (`make install`, `make test`, `make gui`,
  `make ingest`, `make scan`, `make extract`, `make docker-up/down/logs`,
  `make clean`) to match the new CLI.
- Type hints across all helper scripts (function signatures, return types,
  common container types) in `scripts/*.py`.
- Improved error messages for common failure modes: PostgreSQL not
  reachable (includes the host/port/db and the docker-compose hint),
  `claude` CLI missing (points at `$CLAUDE_BIN` and the install docs), and
  "neither OPENAI_API_KEY nor Ollama is available" for the embeddings
  backend picker.

### Fixed

- **Integration tests now pass on a green CI.** Stripped the PG17-only
  `\restrict` / `\unrestrict` psql meta-commands from `sql/schema.sql`
  (psycopg2 in the test fixture executed them as raw SQL and choked
  with `syntax error at or near "\"`), and rewrote
  `tests/integration/test_memory_query.py::test_trigram_search_on_content`
  to rank rows by `similarity()` directly instead of relying on
  `pg_trgm.similarity_threshold` (default 0.3 was too strict for a short
  keyword vs full-sentence content).

### Schema

- `scripts/schema.sql` (additive — does not change any column or
  constraint). Authoritative `pg_dump --schema-only` of the live
  `claude_memory` database. Useful for fresh-DB bootstrap from anywhere
  in the repo without needing the GUI to discover what tables it expects.

## [0.1.0-beta] — 2026-04-18

### Added

- Initial public release
- Docker Compose stack with Postgres 16 + pgvector + GUI + optional Ollama
- Dockerfile for the Streamlit GUI (Python 3.12-slim)
- Unit test suite (79 tests across ingestion, skill scan, prompt scan,
  memory extraction, title generation, and an import smoke test)
- `pytest.ini` + `requirements-dev.txt` + CI job running pytest
- Core database schema with 11 tables
- Session ingestion for Claude Code JSONL files
- Memory extraction via Claude CLI or Anthropic API
- Skill scanner for `~/.claude/skills/` directories
- Prompt scanner for `CLAUDE.md` files
- Windsurf plan ingestion
- Semantic search via pgvector (OpenAI or Ollama backends)
- Context pre-loader hook for new Claude sessions
- Temporal knowledge graph (entities, relationships, mentions)
- Self-reflecting memory engine (dedup, contradictions, stale, consolidate)
- Conversation title auto-generation
- Streamlit GUI with 14 pages
- Calendar view with month / week / day views
- Knowledge graph visualization (streamlit-agraph)
- SQL console
- launchd integration for scheduled ingestion, extraction, and backup
- One-shot installer (`scripts/install.sh`)
- MIT License

### Documentation

- README with quick start and architecture diagram
- CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- `docs/architecture.md` — full technical reference
- `docs/INSTALLATION.md` — detailed setup
- `docs/USAGE.md` — workflows and examples
- `docs/FAQ.md` — common questions
- `examples/` — demo data, common queries, example skill, workflows
