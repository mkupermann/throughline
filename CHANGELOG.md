# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
