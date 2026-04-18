---
name: project-context-loader
description: Load relevant memory, decisions and contacts for the current project at the start of a session.
version: 1.2.0
triggers:
  - load context
  - what do I know about
  - project context
  - start session
  - catch me up on
---

# Project Context Loader

Retrieves and summarises everything the memory database knows about the current
project before you start coding. Eliminates the "cold-start" problem where each
Claude Code session begins with zero context.

## What it does

1. Detects the current project from the working directory path or an explicit argument.
2. Queries `memory_chunks` for decisions, patterns, insights and project context.
3. Queries `projects` for team contacts and stored decisions.
4. Queries `entities` for key people and technologies associated with the project.
5. Presents a structured summary and offers to answer follow-up questions.

## Usage

```
load context for fintech-api
```

```
what do I know about project-aurora?
```

```
project context acme-web
```

If you call it without an argument the skill infers the project name from the
current working directory (last path segment).

## Example output

```
## Context for: fintech-api
Last session: 2026-04-01  |  17 memory chunks  |  3 decisions

### Key decisions
1. Use idempotency keys on all payment endpoints (2025-12-02)
2. Encrypt PII at application layer, not DB-only (2026-01-21)
3. Rate-limit /reconcile to 10 req/min per client (2026-02-10)

### Team contacts
- Alex Kim — Engineering Manager (alex.kim@widget.example)
- Priya Nair — Security Auditor; requires written approval for auth/crypto changes

### Patterns to remember
- N+1 fix: always bulk-fetch with ANY($1) in batch endpoints
- Reconciliation p99: was 8.2s, now 340ms after index + bulk query fix

### Error solutions on record
- SSL connection drop: set keepalives_idle=30 in psycopg2 pool config
```

## Implementation

The skill calls the following SQL at startup. Copy and adjust as needed:

```sql
-- Load active memory chunks for the project
SELECT
    category,
    content,
    confidence,
    tags,
    created_at::date AS date
FROM public.memory_chunks
WHERE project_name = :project_name
  AND status       = 'active'
ORDER BY category, confidence DESC;

-- Load contacts and decisions from the projects table
SELECT
    contacts,
    decisions
FROM public.projects
WHERE name = :project_name;

-- Load related entities
SELECT
    entity_type,
    name,
    attributes,
    mention_count
FROM public.entities
WHERE project_name = :project_name
ORDER BY mention_count DESC
LIMIT 10;
```

## Configuration

The skill reads `PGDATABASE`, `PGHOST`, `PGPORT` and `PGUSER` from the
environment (same as the rest of the tooling — see `.env.example`).

Override the project name inference with an explicit argument, or set
`DEFAULT_PROJECT` in your shell environment to always start with a specific
project context.

## Tips

- Run this skill at the **start** of every session on a project you haven't
  touched in a few days. The time investment (< 5 seconds) pays for itself
  immediately by surfacing decisions you would otherwise re-discover the hard way.

- Combine with `daily-standup-drafter` in the morning: context loader first,
  then standup draft.

- If the project has no memory chunks yet, the skill says so and offers to help
  you create an initial `project_context` chunk from scratch.
