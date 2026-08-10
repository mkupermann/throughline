# Throughline Architecture

**Status:** current reference (supersedes the German design draft in [architecture.de.md](architecture.de.md))
**Stack:** PostgreSQL 16 + pgvector + pg_trgm | Python 3.10+ | the web UI | MCP

---

## 1. Overview

Throughline is a vendor-neutral memory layer for AI coding assistants. Each assistant
writes its conversation history to its own directory in its own format, and none of them
can read another's. Throughline reads all of them, normalises the result into a single
relational schema, and exposes that schema back to any tool through a CLI, an MCP server,
and a web interface.

Design goals:

- **Vendor neutrality.** No source tool is privileged. Claude Code, Codex CLI, Cursor,
  Zed, Vibe, Hermes, Continue.dev, Cline and Windsurf all enter through the same adapter
  contract and land in the same tables. Support for a tenth tool is a new adapter, not a
  schema change.
- **Local-first.** The database runs on `localhost` and is reachable only from the
  machine that owns the data. Ingestion, search, the GUI and the MCP server all work with
  no network access. Only two optional steps leave the machine — LLM-based memory
  extraction and OpenAI embeddings — and both have local substitutes (the Claude Code CLI
  in headless mode, and Ollama).
- **Idempotent, restartable pipelines.** Every stage can be re-run at any time. Unchanged
  inputs are no-ops; changed inputs are replaced, never duplicated.
- **One store, several access paths.** Relational queries, full-text/substring matching
  and approximate nearest-neighbour vector search all run against the same rows in the
  same engine, in one query when needed.

![Throughline architecture](assets/architecture.svg)

---

## 2. Component architecture

The system is a linear pipeline with a fan-out of consumers at the end.

**Adapters** (`throughline/adapters/`) know one thing each: where a given tool keeps its
sessions and how to read that format. They are pure readers — no database access, no
transaction handling. The contract in `throughline/adapters/base.py` has three members:
`is_present()` (cheap existence check on the tool's data directory), `discover()` (yield
candidate files) and `parse()` (turn one file into `NormalisedConversation` objects). An
adapter that needs to emit several conversations from a single file — a SQLite state
database, for example — returns a list, and the writer handles each independently.

**The adapter registry** (`throughline/adapters/registry.py`) lists the nine built-ins
explicitly and then loads any third-party adapters published under the
`throughline.adapters` entry-point group. Built-ins win on name collisions; a third-party
adapter that fails to import is skipped rather than aborting the run.

**The ingestion writer** (`throughline/adapters/writer.py`) is the only code that writes
to `conversations`, `messages` and `ingestion_log`. It owns the database connection, the
idempotency logic, transaction boundaries, per-file error isolation and the automatic
backfill of the `projects` table. Concentrating this in one place is what keeps individual
adapters small enough to test in isolation.

**PostgreSQL** holds everything: transcripts, extracted memory, the knowledge graph,
embeddings and the ingestion ledger. `pgvector` supplies the vector type and HNSW indexes;
`pg_trgm` supplies trigram indexes for substring search.

**Consumers** read from the same schema:

- **CLI** (`throughline/cli.py`) — 17 subcommands covering ingestion (`ingest`,
  `scan-skills`, `scan-prompts`), enrichment (`extract-memory`, `embed`,
  `generate-titles`, `reflect`), query (`search`, `conflicts`), operations (`status`,
  `doctor`, `backup`, `backfill-projects`, `repair-conversations`, `install-hooks`) and
  `serve` / `version`.
- **MCP server** (`memory_mcp/server.py`) — a FastMCP stdio server exposing nine tools:
  `search`, `recall_entity`, `write`, `supersede`, `forget`, `list_projects`,
  `recent_reflections`, `preload_summary` and `stats`. This is how an agent reads and
  writes memory at runtime. Results are project-scoped by default, derived from
  `CLAUDE_PROJECT_DIR`; setting `THROUGHLINE_PROJECT_SCOPE_STRICT` forbids the
  cross-project opt-out entirely.
- **GUI** (`throughline/api/`) — a the web UI application with fourteen pages: Dashboard,
  Calendar, Search, Semantic, Conversations, Memory, Memory Health, Skills, Knowledge
  Graph, Projects, Prompts, Scheduler, Ingestion and SQL. Page bodies live in
  `web/src/features/`.
- **Scheduled jobs** — launchd plists (`launchd/`) on macOS and systemd timers
  (`systemd/`) on Linux run ingestion, memory extraction and `pg_dump` backups
  unattended.

---

## 3. Data model

Schema of record: `sql/schema.sql`. Three enum types constrain the vocabulary:
`memory_category` (decision, pattern, insight, preference, contact, error_solution,
project_context, workflow), `message_role` (user, assistant, system, tool_result) and
`project_status`.

| Table | Purpose |
| --- | --- |
| `conversations` | One row per session from any tool. `session_id` is a unique UUID; `project_name` is a generated column derived from the last segment of `project_path`. Carries model, entrypoint, git branch, timestamps, token counts, cost and a JSONB metadata envelope. |
| `messages` | One row per turn, foreign-keyed to `conversations` with cascade delete. Keeps both rendered `content` and the original `content_blocks`, plus tool calls, tool name, token count and sidechain flag. |
| `memory_chunks` | Durable extracted facts. Categorised, tagged, project-scoped, confidence-weighted. Supports supersession (`superseded_by`, `superseded_at`, `status`), consolidation (`merged_from`) and expiry (`expires_at`), and tracks access counts. |
| `embeddings` | Vectors for any source row, keyed by `(source_type, source_id, model)`. Separate `embedding_1536` and `embedding_768` columns let two embedding backends coexist. |
| `entities` | Knowledge-graph nodes — people, systems, technologies — deduplicated on `(entity_type, canonical_name, project_name)` with a mention counter and first/last-seen timestamps. |
| `entity_mentions` | Links an entity to the specific message or chunk it appeared in, with a context snippet. |
| `relationships` | Typed, time-bounded edges between entities (`valid_from` / `valid_until`) with a confidence score and provenance. |
| `ingestion_log` | The idempotency ledger: `UNIQUE (file_path, file_hash)` plus the record count written for that hash. |
| `projects` | Project registry with description, contacts and decisions as JSONB, and a lifecycle status. Materialised automatically from observed project names. |
| `prompts` | Indexed reusable prompt templates (CLAUDE.md files, skill prompts) with variables, source path and usage count. |
| `skills` | Indexed `SKILL.md` files with version, trigger phrases, filesystem timestamps and usage count. |
| `memory_reflections` | Audit trail of the reflection engine: what it did, to which chunks, why, and with what confidence. |

The view `v_conversation_stats` aggregates sessions, messages, average tokens and cost per
project.

---

## 4. Ingestion pipeline

![Throughline data flow](assets/data_flow.svg)

`throughline ingest --all` runs every adapter whose data directory exists. Per adapter:

1. **Discover.** The adapter yields candidate files. Formats vary widely — Claude Code and
   Cursor write JSONL per session, Zed and Continue.dev write JSON, Vibe writes a
   directory per session with `meta.json` plus `messages.jsonl`, Cline writes several
   files per task under the VS Code extension's global storage, Hermes maintains a live
   SQLite `state.db` alongside JSON exports, and Windsurf produces plan documents in
   Markdown.
2. **Hash and check.** The writer computes a SHA-256 over the file contents and looks it up
   in `ingestion_log`. A matching `(path, hash)` pair means the file has not changed since
   the last run, and it is skipped without parsing. A known path with a new hash is a
   refresh.
3. **Parse.** The adapter converts the file into one or more `NormalisedConversation`
   objects, each holding `NormalisedMessage` records with roles mapped to the
   `message_role` enum.
4. **Write.** The conversation is upserted on `session_id`; its messages are deleted and
   re-inserted as a block. That replace-per-conversation strategy is what makes
   append-heavy transcripts safe: a growing JSONL file re-ingests cleanly instead of
   accumulating duplicate turns.
5. **Record.** The file hash and message count go into `ingestion_log`, and the transaction
   commits. Errors roll back that one file only and increment an error counter — a single
   malformed session cannot abort an unattended nightly run.

**Deterministic session identity.** `conversations.session_id` is a UUID, but most source
tools do not use UUIDs. Adapters derive one with `uuid.uuid5` over a stable namespace and a
source-specific key (for example `codex:<session_id>`), so the same source session always
maps to the same row across re-runs and across machines. Message UUIDs are derived the same
way.

**Project bucketing.** `project_name` is generated from `project_path`, so an adapter
assigns a project simply by setting that field. After any run that ingested new sessions,
the writer materialises missing rows in `projects` with `ON CONFLICT DO NOTHING`, leaving
manually curated rows untouched.

---

## 5. Memory extraction and reflection

Raw transcripts are searchable but verbose. `throughline extract-memory` runs conversations
through an LLM that returns structured findings: one of the eight `memory_category` values,
a self-contained statement understandable without the source conversation, tags, a project
attribution and a confidence score. The prompt's negative filter matters as much as its
positive one — greetings, mechanical tool calls and restatements of general knowledge are
discarded. The judgement being bought is "is this worth remembering", which is not
expressible as a rule, which is why an LLM does it.

Two backends are supported and chosen at runtime: the Anthropic API when
`ANTHROPIC_API_KEY` is set, otherwise the Claude Code CLI in headless mode, which inherits
the user's existing authentication and requires no key of its own. Transcripts pass through
`throughline/pii.py` first, which redacts API-key shapes, bearer tokens, private-key
headers, credential assignments and email addresses. Redaction is on by default and
disabled only with `THROUGHLINE_REDACT_PII=0`. It is deliberately conservative: a missed
secret is bad, but a chunk whose content has been hollowed out is useless.

A separate pass, `scripts/extract_entities.py` (also launchable from the GUI's Knowledge
Graph page), populates `entities`, `entity_mentions` and `relationships`, producing the
knowledge graph behind the MCP `recall_entity` tool.

`throughline reflect` maintains the memory store over time in four modes: **dedup** (merge
near-duplicates within a category and project), **contradictions** (find chunks that
disagree), **stale** (set `expires_at` on time-bound facts and mark expired ones), and
**consolidate** (write a super-chunk over a cluster of related chunks). Every action is
written to `memory_reflections` with its reasoning. Nothing is hard-deleted by the engine —
chunks are superseded or marked, and the previous state stays queryable. `--dry-run`
reports without writing.

`throughline conflicts` operates one level up. Because all tools share a schema, it can
detect contradictions that no per-tool memory could see: a decision recorded by one CLI
that a later session in another CLI overturned. This cross-tool view is the capability that
a single-vendor memory feature structurally cannot provide.

---

## 6. Semantic search

Embeddings are generated by `throughline embed` and stored in `embeddings`, keyed by
`(source_type, source_id, model)` so a row can carry vectors from several models at once
and re-embedding is an upsert rather than a duplicate. Two backends are supported:

- **OpenAI** `text-embedding-3-small` — 1536 dimensions, written to `embedding_1536`.
- **Ollama** `nomic-embed-text` — 768 dimensions, written to `embedding_768`. Fully local,
  no API key, no data leaving the machine.

`--backend auto` picks OpenAI when a key is present and falls back to Ollama otherwise.
Each dimension has its own partial HNSW index using `vector_cosine_ops`, restricted to rows
where that column is non-null, so an installation that only ever uses one backend pays for
only one index. HNSW rather than IVFFlat because it needs no training pass and tolerates
incremental inserts, which is the actual write pattern here.

Query time: the query string is embedded with the same backend that produced the stored
vectors, then a single SQL statement ranks memory chunks and messages together by cosine
distance, with project scoping applied in the same query rather than filtered afterwards.

Lexical search is a separate, always-available path rather than a degraded mode. GIN
trigram indexes on `memory_chunks.content` and `messages.content` back the substring and
keyword queries used by the GUI's Search page and by project-scoped lookups, and they work
with no embedding backend configured at all. An installation with no OpenAI key and no
Ollama is still fully usable; it simply has one fewer ranking method.

---

## 7. Design decisions

**PostgreSQL rather than a dedicated vector database.** The workload is relational first
and vector-searchable second. Conversations own messages, entities own relationships,
projects aggregate both, and the ingestion ledger enforces uniqueness — all of which a
vector store would push back into application code or a second system. The schema depends
on features a vector store does not have: JSONB columns with GIN indexes, array columns,
enum types, generated columns, foreign keys with cascade delete, views, and joins from
`embeddings` back to `memory_chunks` that filter by project and category in the same
statement as the ANN ranking. One engine serves all three access patterns with nothing to
keep in sync.

**An explicit adapter registry rather than auto-discovery.** The nine built-ins are listed
by import path in `registry.py`. Import errors therefore surface immediately and load order
is deterministic, which matters when adapter behaviour is being debugged. Extensibility is
not sacrificed: third-party adapters arrive through the `throughline.adapters` entry-point
group and are picked up at runtime with no change to this file.

**Idempotency by content hash rather than modification time.** Timestamps are unreliable
across filesystem copies, cloud sync and restores; a SHA-256 of the file's contents is not.
This is what makes it safe for the scheduled job to re-scan every tool's directory in full,
once an hour, at negligible cost.

**Local-first as a hard constraint, not a deployment option.** The database binds to
localhost and authenticates as the operating-system user. Transcripts contain credentials,
client names and unreleased work, and the only defensible default for that data is that it
does not leave the machine. The two stages that can call an external API are optional, have
local substitutes, and redact before sending.

**Supersession rather than deletion.** Memory that turns out to be wrong is marked, not
erased — `superseded_by`, `status` and `memory_reflections` preserve both the old fact and
the reason it was replaced. An audit trail of what the system used to believe is worth more
than a clean table, and the explicit `forget` tool remains available for the cases that
genuinely require removal.

---

## 8. Related documents

- [INSTALLATION.md](INSTALLATION.md) — setup and first ingest
- [USAGE.md](USAGE.md) — CLI and GUI walkthrough
- [ADAPTER_DEVELOPMENT.md](ADAPTER_DEVELOPMENT.md) — writing a new adapter
- [DEPLOYMENT.md](DEPLOYMENT.md) — scheduled jobs, Docker, backups
- [PERFORMANCE.md](PERFORMANCE.md) and [BENCHMARKS.md](BENCHMARKS.md) — measured behaviour
- [architecture.de.md](architecture.de.md) — the original German design draft
