# Release Hardening Design

## Goal

Make Throughline's source checkout, installed wheel, Docker deployment, persistent database upgrades, and web UI tell one truthful and verifiable story.

## Scope

The release must fix the reviewed defects in packaging, refresh integrity, Docker exposure, migrations, backup permissions, systemd defaults, CI enforcement, documentation drift, and frontend reliability. The current GUI design stays intact; this is functional hardening, not a visual redesign.

## Architecture

Runtime functionality belongs under importable packages. Top-level scripts remain thin direct-execution wrappers where compatibility requires them. The CLI, MCP server, scheduler, and embedding layer must never depend on a source checkout.

Conversation refresh remains one database transaction. It updates every normalized conversation field and removes derived records tied to messages before replacing those messages, preventing stale embeddings and entity mentions.

Docker remains single-user and local-only. Published services bind to loopback, credentials are configurable, web and MCP processes run without root privileges, and readiness reflects database availability. A migration step runs before application services against persistent volumes.

CI tests the artifact users receive. It builds a wheel, installs it into a clean environment, exercises all entry points, validates migration ordering, enforces Python and frontend quality gates, and runs database-backed integration tests.

## GUI

Keep the existing eight-route information architecture and visual language. Fix Timeline's recency behavior deterministically. Verify loading, empty, degraded, and database-unavailable behavior through tests. Type-check and build the production bundle, and keep committed frontend assets synchronized.

## Security and Operations

PostgreSQL and optional Ollama ports bind to `127.0.0.1`. Database credentials come from environment configuration rather than a public fixed password. The remote-bind bypass is set only by the controlled Compose deployment. Backups use owner-only permissions. systemd units use the current `throughline` database default. Containers use pinned version tags where practical and an unprivileged application user.

## Documentation

README, architecture, deployment, security, installation, release notes, and examples must match current routes, Python support, ingestion behavior, model backends, packaging, migrations, and port exposure. Obsolete claims are corrected rather than preserved as historical architecture.

## Acceptance Criteria

- A clean wheel contains every runtime dependency and all console entry points start outside the checkout.
- Refresh updates all conversation fields and leaves no message-derived orphan rows.
- Docker configuration exposes no unauthenticated data service beyond loopback and upgrades persistent databases before application startup.
- Backup files are owner-only and systemd targets the documented database.
- Ruff, Black check, Python tests, frontend tests, TypeScript, frontend build, wheel smoke tests, migration validation, and Compose validation pass.
- All eight GUI routes have functional API-path coverage, including degraded states where applicable.
- Documentation contains no known contradictions identified in the review.

## Out of Scope

Authentication for multi-user/network deployments, a visual redesign, a new database abstraction, and compatibility promises beyond documented macOS, Linux, and WSL2 support.
