# Throughline Benchmarks

The first section, [Recall and public benchmarks](#recall-and-public-benchmarks),
measures how well the Rust runtime finds notes (2026-10-31). Everything from
[Historical numbers](#historical-numbers) on is older.

## Recall and public benchmarks

Measured on 2026-10-31 with the Rust runtime. Three questions: which ranking
`recall` should use by default, which weights the weighted ranking needs, and
how Throughline does on a public long-term memory benchmark with local models
only. Every bar below was written down before the run it judges and was not
moved afterwards. A result that missed its bar is reported as such.

### Setup

- One installation: an Apple M5 Max with 36 GB, PostgreSQL 16 with pgvector,
  Ollama on the same machine.
- A copy of one real corpus, not the live database: about 1,600 current notes
  (1,595 at the start of part B), every one with a vector.
- Embedding model `granite-embedding-multilingual:278m` for notes and questions.
- Every recall number comes from `throughline recall-eval` or from re-ranking
  its `--dump-parts` output offline (the grid and the random weights of part
  B). `recall-eval` runs the real recall path and counts no use, so measuring
  changed no note's strength. The extraction numbers of part C come from
  `extract-memory`.
  k = 5, seed 7. MRR counts down to rank 50.

### The test set

74 questions with known answer notes, frozen by SHA-256 before the first run.
The questions and notes are private, so the set is not in this repository; the
method works on any corpus (see [Reproducing](#reproducing-the-recall-numbers)).

- 60 come from the earlier multilingual set: 30 questions and their 30
  translations. 25 ask in the language of their note, 25 in another language,
  10 are unclear.
- 14 are modelled on questions the corpus owner really asked in the 90 days
  before. He approved them; he did not write them. The rule asked for questions
  he wrote himself, so this is a deviation. Their answer notes were found by
  pooling the top results of every method plus a manual search, and checked
  against the full note text.
- A bias named before the run: the cross-language questions were built so that
  no word of the question appears in its note. That works against full text on
  purpose. The results are shown with and without them.

### Part A: the default ranking

The rule was fixed after the first recall measurement (30 questions), before
this one: full text stays the default only if it beats the best alternative by
at least 5 points hit@5 on the whole set. Otherwise the alternative with the
higher hit@5 becomes the default, on a tie the one with the higher MRR.

hit@5, with MRR for the whole set:

| Method | All 74 | Same language (25) | Cross-language (25) | Unclear (10) | Owner's questions (14) | Without cross-language (49) |
| --- | --- | --- | --- | --- | --- | --- |
| Full text (the default until then) | 50.0 % (MRR 0.388) | 80.0 % | 8.0 % | 50.0 % | 71.4 % | 71.4 % |
| Weighted, old weights | 70.3 % (MRR 0.596) | 92.0 % | 36.0 % | 80.0 % | 85.7 % | 87.8 % |
| Cosine | 94.6 % (MRR 0.818) | 100.0 % | 88.0 % | 100.0 % | 92.9 % | 98.0 % |
| Random | 0.0 % (MRR 0.010) | 0.0 % | 0.0 % | 0.0 % | 0.0 % | 0.0 % |
| Phrase match | 0.0 % (MRR 0.000) | 0.0 % | 0.0 % | 0.0 % | 0.0 % | 0.0 % |

Paired bootstrap over 36 groups of questions that share answer notes, 1,000
draws: cosine minus full text +44.6 points (95 % interval +34.7 to +53.4),
weighted minus full text +20.3 points (+11.0 to +28.8).

By the rule, full text was 44.6 points behind instead of 5 ahead. Cosine had
the highest hit@5 and became the default ranking of mode `current`. It also
leads without the cross-language questions (98.0 % against 71.4 %) and on the
owner's questions (92.9 % against 71.4 %), so the bias named above does not
decide the outcome. Random and phrase match found nothing in the top five; for
random the expected value over these candidate pools was 3.4 %.

### Part B: weights of the weighted ranking

- The questions were split into a calibration part (42 questions) and a test
  part (32 questions) by group, so a question and its translation never land on
  different sides. Seed 7.
- A grid with step 0.05 over the six weights (cosine, word overlap, recency,
  reliability, strength, provenance; sum 1) has 53,130 vectors. It ran on the
  calibration part only. The best was cosine 0.85 and reliability 0.15, the
  other four 0. It hit 95.2 % there, the old weights 69.0 %.
- The bar, set before: the calibrated weights must beat the 95th percentile of
  20 random weight vectors on the test part, and they become the default of
  `rank=weighted` only if they lead the old weights there by at least 5 points
  hit@5.
- The test part ran once, through the real path:

| Test part, 32 questions | hit@5 | MRR |
| --- | --- | --- |
| Calibrated (0.85 / 0 / 0 / 0.15 / 0 / 0) | 93.8 % | 0.771 |
| Old weights (0.45 / 0.20 / 0.10 / 0.10 / 0.10 / 0.05) | 71.9 % | 0.632 |
| Cosine | 93.8 % | 0.772 |
| Full text | 53.1 % | 0.433 |
| Random | 6.3 % | 0.030 |

The 20 random vectors (Dirichlet, seed 7) hit between 1 and 20 of the 32
questions; their 95th percentile is 18.1. The calibrated weights hit 30, above
all 20, and 21.9 points over the old weights. Both bars are met, so they are the
default of `rank=weighted` now. Said plainly: the calibrated vector is cosine
with a little reliability, and on the test part it ties plain cosine. On this
set the weighted sum adds nothing over cosine.

### Part C: LoCoMo with local models

[LoCoMo](https://github.com/snap-research/locomo) (Maharana et al., 2024) is a
public benchmark for long-term conversational memory: 10 long dialogues
between two speakers, 272 sessions, 1,986 questions in five categories
(single-hop, multi-hop, temporal, open-domain, adversarial), each with the
turns that hold the answer. Its licence is CC BY-NC 4.0. No part of the data,
and no result that quotes it, is in this repository.

- **Separate database.** The data goes into `throughline_bench` only. The
  `locomo` adapter refuses any other database name, and `ingest --source all`
  does not include it. It reads the file named by `THROUGHLINE_LOCOMO_PATH`;
  there is no default path.
- **Mapping.** Each LoCoMo session becomes one Throughline conversation, each
  dialogue one project. Turns keep their session date.
- **Same pipeline as in use.** Ingest, `extract-memory`, `embed`, recall. Local
  Ollama only: `qwen3.6:27b` for extraction and answers, granite for vectors,
  thinking off and a context of 16,384 tokens.
- **Extraction profile.** The production prompt is written for coding sessions
  and skips small talk. A probe on 2 dialogues (38 sessions) gave 0 notes per
  session with it. The stop rule set before the probe (fewer than one note per
  session) switched to `THROUGHLINE_EXTRACT_PROFILE=personal`, which gave 11.1
  notes per session at 42.8 s per session (measured over the 19 sessions before
  the machine went to sleep).
- **Retrieval.** `recall-eval --mode locomo` asks every question in its
  dialogue's project, with the default rank (cosine) and with full text and
  weighted for comparison. A hit means a returned note comes from a session
  that holds one of the question's evidence turns. That is session level:
  notes do not record which turn they came from. Per category, with n, at
  k = 5 and k = 10: `hit`, the share of questions with a note from an evidence
  session in the top k, and `coverage`, the mean share of a question's evidence
  sessions that have a note in the top k. The adversarial category is reported
  apart and stays out of the headline.
- **Answers.** A context pack for the question's project, then the production
  answer prompt on `qwen3.6:27b`. The same model judges each answer three times
  with the yes/no template of the Mem0 paper, and the majority counts. The same
  model answering and judging is a limitation, and a local judge's score is not
  comparable to scores judged by a hosted model. The judge is checked against
  50 answers labelled by hand; agreement below 80 % means the answer score is
  reported as unreliable.

> **Placeholder.** Results: to be filled from `results-C-*`.

### Limitations

- One person, one machine, one corpus, one embedding model. The numbers hold
  for this installation.
- Measured with granite on one installation. An install on the default
  `nomic-embed-text` gets cosine with nomic, which is weaker across languages.
  In the earlier multilingual measurement on 60 of these questions, nomic found
  92 % of the same-language and 8 % of the cross-language questions at k = 5;
  full text found 80 % and 8 % of them here. The 74-question run was not
  repeated with nomic.
- 74 questions: one question is 1.4 points on the whole set and 3.1 points on
  the test part of part B.
- Only mode `current` was measured. The cosine default applies there only;
  `as_of`, `associative` and `grounded` keep full text when no rank is named.
- The weighted ranking with the old weights read the clock and the use
  counts, so a re-run of part A can move it slightly. Full text and cosine
  depend on neither.
- The owner's questions were approved by him, not written by him (see above).
- MRR here counts down to rank 50. The earlier multilingual measurement
  counted to rank 10, so its MRRs are not comparable.

### What the default costs: a vector per note

Cosine needs a vector for every note. A note now gets its vector right after it
is written: a pin, a write over HTTP or MCP, a replacement, a merge, a kept
note, an extracted or imported note. That is one call to the embedding model
per note, with at most 3 s of waiting. A failure never fails the write. If
Ollama is down or too slow, further calls pause for 60 s, and in that time the
default recall ranks by words. Extraction now makes one embedding call per
note, on the same Ollama that runs the extraction model. The time per note was
not measured on its own, and the extraction timings in part C were taken
before this existed. A note the write could not embed is ranked by its words
until `throughline embed` gives it a vector, and the answer says how many such
notes it saw. The shipped `launchd/` and `systemd/` templates run the Rust
`embed` hourly at minute 15; they help only when they are given the server's
`OLLAMA_EMBED_MODEL`, because vectors of another model are not read.

### Reproducing the recall numbers

Write a set of questions from your own notes, as a JSON array of
`{"q": "...", "project": "..." or null, "expected": [note ids]}`, and keep it
outside the repository if the notes are private. Then:

```bash
# Part A: every method on the whole set
throughline recall-eval --set my-set.json --k 5 --seed 7 --json

# Part B: the six score parts of every candidate, for an offline grid
throughline recall-eval --set calibration.json --k 5 --seed 7 --json \
  --dump-parts parts.json

# Part B: one weight vector on the held-out questions, through the real path
throughline recall-eval --set test.json --k 5 --seed 7 --json \
  --weights 0.85,0,0,0.15,0,0
```

## Historical numbers

Historical, reproducible numbers from a real developer laptop. Nothing in this
part is a synthetic stress test — every measurement was made against the
author's Claude Code corpus in `~/.claude/projects/` before the multi-adapter
runtime. They are not claims about the current nine-adapter ingest command.

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

Measurements taken on 2026-04-18 against a warm Claude Code-only database of
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

The historical `scripts/ingest_sessions.py` command walked
`~/.claude/projects/`, hashed each JSONL file with SHA-256, skipped if already
in `ingestion_log`, parsed
line-by-line with `json.loads`, inserted messages one at a time, and committed
once per source file.

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

The historical `scripts/extract_memory.py` command sent a conversation window to the
`claude` CLI in headless mode (`claude -p`), parses JSON out of stdout,
and inserts into `memory_chunks`.

| Metric | Value |
|---|---|
| CLI round-trip (Sonnet, ~4k token window) | 6 – 15 s per conversation |
| Chunks produced per conversation (median) | 3 – 6 |
| Conversations processed per daily run (default cap) | 20 |
| Total daily extraction time | 2 – 5 min |
| Sleep between calls (avoid local rate limit) | 2 s |

**Cost:** this historical run used the user's existing Claude Code CLI and
inherited its authentication and configured model. It is not a current backend
configuration guide.

---

## Self-Reflection

The historical `scripts/reflect_memory.py` command paired candidate chunks via cosine
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
