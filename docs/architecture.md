# Throughline Architecture

**Status:** current reference. A German version is available in [architecture.de.md](architecture.de.md).
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
- **Local-first.** Native services bind to loopback. Compose maps PostgreSQL, the web UI,
  and optional Ollama to loopback host ports. Ingestion, search, the GUI, and MCP work with
  no network access when local model backends are selected. A hosted model receives only
  the excerpt or transcript for the operation that uses it.
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
sessions and how to read that format. They are pure readers, with no database access or
transaction handling. `discover()` yields ingestible files and `parse()` converts one file
to `NormalisedConversation` objects. `discover_all()` and `excluded_reason()` let an adapter
report candidates that are intentionally excluded. An adapter that needs to emit several
conversations from one source returns a list, and the writer handles each independently.

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

- **CLI** (`throughline/cli.py`) — packaged commands covering ingestion (`ingest`,
  `scan-skills`, `scan-prompts`), enrichment (`extract-memory`, `embed`,
  `generate-titles`, `reflect`), query (`search`, `conflicts`), operations (`status`,
  `doctor`, `backup`, `backfill-projects`, `repair-conversations`, `install-hooks`,
  `migrate`) and `serve` / `version`. Direct `scripts/*.py` files remain compatibility
  wrappers; installed users run `throughline <command>`.
- **MCP server** (`memory_mcp/server.py`) — a FastMCP stdio server exposing nine tools:
  `search`, `recall_entity`, `write`, `supersede`, `forget`, `list_projects`,
  `recent_reflections`, `preload_summary` and `stats`. This is how an agent reads and
  writes memory at runtime. Results are project-scoped by default, derived from
  `CLAUDE_PROJECT_DIR`; setting `THROUGHLINE_PROJECT_SCOPE_STRICT` forbids the
  cross-project opt-out entirely.
- **GUI** (`throughline/api/` and `web/src/`) — one FastAPI process serves the JSON API and
  built React SPA. It has eight route components: Overview, Find, Timeline, Curate, Project,
  Detail, Operate, and Console. Overview, Find, Timeline, Curate, Operate, and Console are
  in the main navigation; project and detail routes open from records.
- **Scheduled jobs** — launchd plists (`launchd/`) on macOS and systemd timers
  (`systemd/`) on Linux run ingestion, memory extraction and `pg_dump` backups
  unattended.

---

## 3. Data model and migrations

The versioned schema lives in packaged `throughline/migrations/NNN_*.sql` files.
`throughline migrate` applies them in ordinal order and records each successful
file in `public.applied_migrations`. `sql/schema.sql` remains a schema snapshot
for inspection and CI validation, not the new-installation path. Three enum
types constrain the vocabulary:
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

Compose waits for PostgreSQL readiness, runs `throughline migrate`, then starts web and MCP
only when migration succeeds. On a native installation, run `throughline migrate` after
`createdb` and every upgrade. An older schema initialized from `sql/schema.sql` is detected
and baselined before later migrations apply; migration history is never rewritten.

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
4. **Write.** The conversation is upserted on `session_id`; every normalised field is
   replaced, including nullable values, and its messages are replaced as a block. Before the
   replacement, message-derived embeddings and entity mentions for that conversation are
   removed in the same transaction. Advisory locks prevent a concurrent derived-data producer
   from writing against messages that have just been replaced. A growing transcript therefore
   refreshes cleanly instead of accumulating duplicate or stale data.
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

**Generated sessions.** Throughline's own model calls can create source sessions. The writer
recognizes the known self-referential prompts, drops those source files before any conversation
is written, and records a zero-row decision in `ingestion_log`. `conversations.generated_by`
is retained for legacy rows imported before that guard; `backfill_generated_by` labels those
rows, which listings, search, charts, and answers exclude by default.

---

## 5. Memory extraction and reflection

Raw transcripts are searchable but verbose. `throughline extract-memory` runs conversations
through an LLM that returns structured findings: one of the eight `memory_category` values,
a self-contained statement understandable without the source conversation, tags, a project
attribution and a confidence score. The prompt's negative filter matters as much as its
positive one — greetings, mechanical tool calls and restatements of general knowledge are
discarded. The judgement being bought is "is this worth remembering", which is not
expressible as a rule, which is why an LLM does it.

Extraction, title generation, reflection, and answers are model operations. A
selected hosted endpoint or the Claude CLI can receive the relevant transcript,
excerpt, or chunk pair; choose a local backend only where that boundary is
acceptable. Transcripts pass through `throughline/pii.py` first, which redacts
API-key shapes, bearer tokens, private-key headers, credential assignments and
email addresses. Redaction is on by default and disabled only with
`THROUGHLINE_REDACT_PII=0`. It is deliberately conservative: a missed secret
is bad, but a chunk whose content has been hollowed out is useless.

A separate packaged job, `python -m throughline.jobs.extract_entities`, populates
`entities`, `entity_mentions` and `relationships`, producing the
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

**Local-first as a hard constraint, not a deployment option.** Native services bind to
loopback. Compose publishes only loopback ports, requires a database password, runs the
application as an unprivileged user, and mounts tool directories read-only. Transcripts can
contain credentials, client names, and unreleased work. The API has no authentication, so a
remote bind requires the operator's own authentication and TLS. See [SECURITY.md](../SECURITY.md).

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
- [architecture.de.md](architecture.de.md) — current German architecture reference
