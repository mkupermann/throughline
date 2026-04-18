# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **PII / secret redaction** in `throughline/pii.py` that runs automatically
  before each transcript is sent to Claude for memory and entity extraction.
  Redacts Anthropic / OpenAI / GitHub / AWS / Google / Slack / Stripe
  API-key shapes, JWTs, bearer tokens, `password=` / `secret=` / `token=`
  assignments, private-key blocks, email addresses, and home-directory
  usernames. Default on. Disable with `THROUGHLINE_REDACT_PII=0` or
  `memory.redact_pii: false`. 18 unit tests in `tests/test_pii.py`.
- MCP server exposing 10 tools for Claude Code integration (search_memory, search_semantic, get_project_context, get_recent_conversations, get_conversation, list_decisions, find_contact, list_entities, get_entity_relations, add_memory)
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
