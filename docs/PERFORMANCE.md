# Performance Tuning

Measured baseline figures are in [BENCHMARKS.md](BENCHMARKS.md). This page
covers the knobs that matter when your history grows past what a default
setup handles comfortably.

## Ingestion

- **Idempotency is the main lever.** Every ingested file is recorded in
  `ingestion_log` with a content hash; unchanged files are skipped without
  parsing. Steady-state ingest cost is therefore proportional to *new*
  session data, not total history. Schedule frequent ingests (see
  [DEPLOYMENT.md](DEPLOYMENT.md)) — they get cheaper, not more expensive.
- **First ingest of a large history** is dominated by JSON parsing and
  message inserts. Run it once, unattended; subsequent runs are incremental.
- If a single tool's directory is huge (tens of thousands of sessions),
  ingest it separately with `throughline ingest --source <name>` to keep
  runs observable.

## Semantic search / embeddings

- Embeddings are stored per dimension (`embedding_768`, `embedding_1536`)
  with partial **HNSW** indexes using cosine distance
  (`idx_embeddings_768_hnsw`, `idx_embeddings_1536_hnsw`). HNSW gives good
  recall without the retraining requirement of IVFFlat as data grows.
- Query-time recall/latency trade-off is controlled per session:

  ```sql
  SET hnsw.ef_search = 100;   -- default 40; higher = better recall, slower
  ```

- Embedding generation (`scripts/generate_embeddings.py`) is the expensive
  step, not search. Run it as a background job; freshly inserted chunks are
  usable immediately and picked up by the next embedding pass.
- Local embeddings via Ollama (`docker compose --profile embeddings`) avoid
  network latency and API cost; 768-dimension local models are markedly
  cheaper to index and search than 1536-dimension API models at comparable
  practical quality for this workload.

## PostgreSQL

Defaults are fine into the hundreds of thousands of messages. Beyond that:

```conf
# postgresql.conf — laptop-scale suggestions
shared_buffers = 1GB            # 25% of RAM you are willing to give it
maintenance_work_mem = 512MB    # speeds up HNSW index builds significantly
work_mem = 64MB                 # sorts/hashes in search queries
```

- `maintenance_work_mem` matters most: HNSW index builds that exceed it fall
  back to a much slower path.
- Text search uses trigram (`pg_trgm`) GIN indexes; they are
  insert-amortised and need no tuning, but benefit from occasional
  `VACUUM ANALYZE` after bulk ingests.
- The GUI's heaviest queries are conversation lists filtered by project and
  date, covered by `idx_conversations_project_name` and
  `idx_conversations_started_at`.

## Memory extraction and reflection

- Extraction (`throughline extract-memory`) calls an LLM per conversation
  batch — cost scales with new conversations, and processed ones are marked,
  so it is incremental like ingestion.
- Reflection (`throughline reflect`) runs pairwise dedup on memory chunks;
  it is quadratic in candidate pairs but candidates are pre-filtered by
  similarity, so run it after embedding backfill, not before.

## When something is slow

1. `throughline status` — chunk counts, embedding coverage, last ingest.
2. `throughline doctor` — configuration and connectivity problems.
3. `EXPLAIN ANALYZE` the slow query; check it hits the indexes above.
4. Check embedding coverage: semantic search silently degrades to fewer
   candidates when coverage is low — the fix is running the embedding
   backfill, not tuning PostgreSQL.
