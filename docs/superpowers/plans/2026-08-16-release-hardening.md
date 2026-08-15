# Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Throughline installable, safely deployable, migration-correct, GUI-verified, and release-gated.

**Architecture:** Move runtime code into packaged modules, make persistence refresh complete, harden local-only deployment, and test the built artifact. Preserve the current GUI design while making its behavior deterministic and covered.

**Tech Stack:** Python 3.10+, PostgreSQL 16/pgvector, FastAPI, React/TypeScript/Vite, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-16-release-hardening-design.md`

## Global Constraints

- Keep the current eight-route GUI and visual design.
- Preserve direct script execution where documented.
- Keep the product single-user and local-only by default.
- No silent migration or data-loss behavior.
- Every production fix starts with a regression test.

---

### Task 1: Package all runtime code

**Files:** `throughline/cli.py`, `throughline/embedding.py`, `memory_mcp/server.py`, new packaged job modules, `scripts/*.py`, `pyproject.toml`, packaging tests.

- [ ] Add wheel-content and clean-install entry-point tests and confirm failure.
- [ ] Move or wrap runtime implementations under installable packages.
- [ ] Remove source-checkout assumptions from CLI, MCP, scheduler, and embedding paths.
- [ ] Build and smoke-test a clean wheel.

### Task 2: Correct refresh integrity

**Files:** `throughline/adapters/writer.py`, schema/migrations, writer integration tests.

- [ ] Add tests for complete field refresh and derived-row cleanup and confirm failure.
- [ ] Update every normalized conversation field on conflict.
- [ ] Delete or invalidate message-derived embeddings and entity mentions before message replacement.
- [ ] Run writer and ingestion integration tests.

### Task 3: Repair migration lifecycle

**Files:** `sql/migrations/*`, `scripts/migrate.py` or packaged equivalent, Compose startup, migration tests.

- [ ] Add duplicate-ordinal and upgrade-path tests and confirm failure.
- [ ] Renumber the duplicate migration safely without changing already-recorded migration identities destructively.
- [ ] Add automatic migration gating before web and MCP startup.
- [ ] Test fresh schema and sequential upgrades.

### Task 4: Harden Docker and operations

**Files:** `docker-compose.yml`, `Dockerfile`, `.env.example`, `scripts/backup.sh`, `systemd/*.service`, API health/settings tests.

- [ ] Add configuration tests for loopback-only ports, credentials, user, backup permissions, systemd defaults, and readiness.
- [ ] Implement safe port bindings and configurable credentials.
- [ ] Run application containers unprivileged and move remote-bind permission to Compose.
- [ ] Set backup umask and current database defaults.
- [ ] Add database-backed readiness and validate Compose configuration.

### Task 5: Harden and verify the GUI

**Files:** Timeline implementation/tests, route/API tests, frontend build artifacts.

- [ ] Reproduce the Timeline failure with deterministic time.
- [ ] Fix recency selection without changing layout.
- [ ] Add or strengthen loading, empty, degraded, and unavailable-state coverage across all routes.
- [ ] Run Vitest, TypeScript and production build; verify committed assets.

### Task 6: Establish enforceable CI quality gates

**Files:** `.github/workflows/ci.yml`, `requirements-dev.txt`, Ruff/Black configuration, affected Python files.

- [ ] Add CI assertions for lint, format, coverage, wheel smoke, migrations and full package syntax.
- [ ] Fix Ruff and Black violations in governed production/test paths.
- [ ] Set a realistic non-regressing coverage floor, emphasizing runtime surfaces.
- [ ] Run the same gates locally.

### Task 7: Reconcile documentation

**Files:** `README.md`, `docs/architecture.md`, `docs/architecture.de.md`, `SECURITY.md`, deployment/installation/release documentation and screenshots if behavior changed visibly.

- [ ] Search for every contradiction identified in the review.
- [ ] Update current behavior, support matrix, security boundaries and migration workflow.
- [ ] Run documentation checks and inspect links/assets.

### Task 8: Release verification and publication

**Files:** all intended changes.

- [ ] Run the complete Python, database, frontend, lint, format, wheel, migration, Compose and documentation gates from clean state.
- [ ] Inspect the full diff and confirm no unrelated or secret files.
- [ ] Commit with a terse release-hardening message.
- [ ] Push `agent/release-hardening` and open a draft PR against `main`.
