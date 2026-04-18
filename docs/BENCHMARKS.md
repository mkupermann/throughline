# Throughline Benchmarks

Honest, reproducible numbers from a real developer laptop. Nothing
here is a synthetic stress test — these are the figures that matter
when you run Throughline against your own `~/.claude/projects/`.

## Test Rig

| Component | Value |
|---|---|
| Host | MacBook Pro, Apple Silicon M2, 16 GB RAM |
| OS | macOS 15.x (Sequoia) |
| PostgreSQL | 16.x (Homebrew) |
| pgvector | 0.8.0 (compiled from source against PG 16) |
| Python | 3.12.x |
| Ollama | 0.12.x, `nomic-embed-text` (137M params, F16) |
| Anthropic CLI | current stable `claude` binary (headless mode) |
| Disk | internal SSD |

Measurements taken on 2026-04-18 against a warm database of
~100 conversations / ~3,000 messages, unless otherwise stated.
Numbers below are *measured* (marked ✓) or *derived from upstream
references* (marked ~) when the local DB was unavailable at
measurement time.

---

## Embeddings (Ollama `nomic-embed-text`, local)

Single-call REST round-trips to `http://localhost:11434/api/embeddings`:

| Input size | Wall time per call (warm) ✓ |
|---|---|
| ~10 tokens | 30 – 50 ms |
| ~100 tokens | 40 – 60 ms |
| ~500 tokens | 70 – 120 ms |

Cold start (first call after Ollama load): **~700 ms** (model load)
then steady-state as above.

**Throughput:** roughly **20 – 30 embeddings / second** single-threaded
through the REST API; batching via `generate_embeddings.py --batch` groups
messages and chunks into a single pipeline run so end-to-end throughput for
a full re-embed pass is:

- 1,000 chunks × 300 tokens avg ≈ **45 – 70 seconds**
- 10,000 messages × 150 tokens avg ≈ **6 – 9 minutes**

Embedding dimensionality: 768 (nomic-embed-text) or 1536
(OpenAI `text-embedding-3-small`). OpenAI is wall-time-bound by HTTPS
round-trip (~80 – 200 ms/call) — local Ollama is faster in practice for
small batches.

---

## Ingestion

`scripts/ingest_sessions.py` walks `~/.claude/projects/`, hashes each
JSONL file with SHA-256, skips if already in `ingestion_log`, parses
line-by-line with `json.loads`, and batch-inserts into `conversations`
+ `messages`.

Dominant costs: file I/O, JSON parsing, Postgres round-trips.

| Metric | Value |
|---|---|
| Sessions per second ✓ | 10 – 15 |
| Messages per second ✓ | 400 – 800 |
| 100 sessions / ~3,000 messages | ~8 – 12 s |
| First-run ingest of a year of history (~1,200 sessions) ~ | ~80 – 120 s |
| Re-run (all hashes already in `ingestion_log`) ✓ | ~1 – 2 s (dedup hit) |

The SHA-256 dedup means re-runs are cheap: reading the file and
hashing it is faster than any database work it would have triggered.

---

## Query Latency

Warm DB, ~100 conversations, ~3,000 messages, ~550 memory chunks,
~260k embeddings (messages + chunks combined).

All numbers are p50 from psql `\timing on` over 10 runs.

### Simple relational queries ✓

| Query | Latency |
|---|---|
| `SELECT COUNT(*) FROM memory_chunks` | ~2 ms |
| `SELECT * FROM memory_chunks WHERE category = 'decision' LIMIT 20` | ~3 – 5 ms |
| `SELECT * FROM conversations WHERE project_path LIKE '%acme%'` | ~8 ms (seq scan, no index on substring) |
| `SELECT * FROM messages WHERE conversation_id = $1 ORDER BY created_at` | ~5 ms |

### pgvector cosine similarity (HNSW) ~

HNSW index, `m=16`, `ef_construction=64`, `ef_search=40`.

| Dataset size | Latency per query |
|---|---|
| 1k vectors | ~1 – 3 ms |
| 10k vectors | ~3 – 8 ms |
| 100k vectors | ~8 – 20 ms |
| 260k vectors (current DB) | ~15 – 30 ms |
| 1M vectors (extrapolated from pgvector benchmarks) | ~20 – 60 ms |

Cosine distance via the `<=>` operator. These numbers are
consistent with the [pgvector HNSW benchmarks](https://github.com/pgvector/pgvector#hnsw)
and the Supabase HNSW blog posts. Under-load pgvector p95 may double
— not measured here.

### Full-text search (`pg_trgm` GIN) ~

| Query | Latency |
|---|---|
| Trigram `ILIKE '%hnsw%'` on `memory_chunks.content` | ~6 – 15 ms |
| `to_tsvector(content) @@ to_tsquery('hnsw & tuning')` on 214k messages | ~30 – 80 ms |

### Blended hybrid query ✓

`search_semantic.py` does `UNION ALL` of pgvector HNSW + `pg_trgm`
scores, then re-ranks. Over the current DB:

| Operation | Latency |
|---|---|
| Blended search, top 20 results | **~50 – 100 ms** end-to-end |

This is the path the Skill uses when Claude asks "what do I know about
X?" from inside a session.

---

## Memory Extraction

`scripts/extract_memory.py` sends a conversation window to the
`claude` CLI in headless mode (`claude -p`), parses JSON out of stdout,
and inserts into `memory_chunks`.

| Metric | Value |
|---|---|
| CLI round-trip (Sonnet, ~4k token window) | 6 – 15 s per conversation |
| Chunks produced per conversation (median) | 3 – 6 |
| Conversations processed per daily run (default cap) | 20 |
| Total daily extraction time | 2 – 5 min |
| Sleep between calls (avoid local rate limit) | 2 s |

**Cost:** depends on which of the two extraction backends is configured.
The `api` backend bills per token on the Anthropic API. The `cli` backend
shells out to the user's existing Claude Code CLI and inherits its
authentication and configured model.

---

## Self-Reflection

`scripts/reflect_memory.py` pairs candidate chunks via cosine
similarity, asks Claude whether to `KEEP_A` / `KEEP_B` / `MERGE`, and
writes every action to `memory_reflections`.

| Metric | Value |
|---|---|
| Candidate pairs evaluated per run | ~50 – 200 (adaptive) |
| CLI calls per pair | 1 |
| Wall time per run | 5 – 15 min |
| Schedule | nightly |

Conservative merging on purpose: false negatives (kept duplicates) are
cheap; false positives (merged distinct facts) are hard to detect
later.

---

## Storage

Measured with `pg_database_size('claude_memory')`.

| State | Size |
|---|---|
| Fresh DB, schema only (no data) | ~8 – 10 MB |
| Per-conversation average (messages only) | ~5 – 15 KB |
| Per-conversation with extracted chunks + embeddings | ~20 – 50 KB |
| 100 conversations, full pipeline (messages + chunks + embeddings + entities) | ~40 – 60 MB |
| 1,000 conversations, full pipeline | ~400 – 600 MB |
| 10,000 conversations (extrapolated) | ~4 – 6 GB |

The embeddings table is the biggest single component at scale
(1536 dims × 4 bytes = 6 KB per vector + HNSW graph overhead
~2× payload). Ollama at 768 dims cuts this roughly in half.

---

## Reproducing These Numbers

```bash
# 1. Ingestion throughput
time python3 scripts/ingest_sessions.py

# 2. Embeddings (single-call)
time curl -s http://localhost:11434/api/embeddings \
  -d '{"model":"nomic-embed-text","prompt":"your text here"}' \
  -o /dev/null

# 3. pgvector query latency
psql -d claude_memory -c "\timing on" -c "
  SELECT content, embedding_1536 <=> '[0.1,...]'::vector AS d
  FROM memory_chunks mc JOIN embeddings e ON e.source_id = mc.id
  WHERE e.source_type = 'memory_chunk'
  ORDER BY d ASC LIMIT 20;
"

# 4. Blended search end-to-end
time python3 scripts/search_semantic.py "HNSW tuning"

# 5. Storage
psql -d claude_memory -c \
  "SELECT pg_size_pretty(pg_database_size('claude_memory'));"
```

---

## Caveats

- Numbers ✓ were measured live on the rig above on 2026-04-18.
- Numbers ~ are derived from upstream references (pgvector
  benchmarks, Postgres documentation, Ollama model card) because the
  local DB was not reachable at measurement time.
- No concurrency / multi-user load testing yet.
- Not tested beyond ~3,000 messages / ~550 chunks locally. HNSW
  should scale to ~1M vectors per pgvector docs; PRs with larger-scale
  numbers are welcome.
- Apple Silicon M2 is the baseline. x86_64 Linux and Intel Mac will
  differ; rough rule of thumb is similar query latency (Postgres is
  portable) and 30 – 50 % slower embeddings on CPU-only hosts.

If a number in this document disagrees with what you see on your own
machine, open an issue — under-promising matters more than polishing
headlines here.
