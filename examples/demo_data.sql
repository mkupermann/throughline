-- =============================================================================
-- Demo Data for Claude Memory
-- =============================================================================
-- Realistic but entirely fictional data for demonstration purposes.
-- No real names, no real companies. Safe to commit and share publicly.
--
-- Load with:  psql -d claude_memory -f examples/demo_data.sql
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- PROJECTS
-- ---------------------------------------------------------------------------

INSERT INTO public.projects (id, name, description, contacts, decisions, status, created_at, updated_at) VALUES
(1,
 'acme-web',
 'Customer-facing web portal for ACME Corp — React + FastAPI stack, deployed on-prem.',
 '[
   {"name": "Jane Smith",  "role": "Product Owner",     "email": "jane.smith@acme.example"},
   {"name": "Bob Chen",    "role": "Lead Backend Dev",  "email": "bob.chen@acme.example"},
   {"name": "Sara Okafor", "role": "QA Lead",           "email": "sara.okafor@acme.example"}
 ]'::jsonb,
 '[
   {"date": "2025-11-03", "decision": "Use React 18 with Vite instead of CRA — build time 3x faster in benchmarks."},
   {"date": "2025-11-17", "decision": "Stick with FastAPI over Django; team has more async experience."},
   {"date": "2026-01-08", "decision": "Defer WebSocket support to v2 — not in scope for initial launch."}
 ]'::jsonb,
 'active',
 '2025-11-01 09:00:00+01', '2026-03-15 14:22:00+01'),

(2,
 'fintech-api',
 'Internal payment processing microservice. Handles reconciliation with Widget Industries ERP.',
 '[
   {"name": "Alex Kim",    "role": "Engineering Manager", "email": "alex.kim@widget.example"},
   {"name": "Priya Nair",  "role": "Security Auditor",   "email": "priya.nair@widget.example"}
 ]'::jsonb,
 '[
   {"date": "2025-12-02", "decision": "Use idempotency keys on all payment endpoints — learned from incident #42."},
   {"date": "2026-01-21", "decision": "Encrypt PII fields at application layer (not only DB-level) per audit finding."},
   {"date": "2026-02-10", "decision": "Rate-limit reconciliation endpoint to 10 req/min per client — prevents timeout cascade."}
 ]'::jsonb,
 'active',
 '2025-12-01 10:00:00+01', '2026-04-01 09:10:00+02'),

(3,
 'ml-pipeline',
 'Batch ML training pipeline — nightly Airflow DAGs, model artefacts pushed to S3-compatible storage.',
 '[
   {"name": "Dan Reyes",   "role": "ML Engineer",       "email": "dan.reyes@acme.example"},
   {"name": "Lin Zhao",    "role": "Data Engineer",     "email": "lin.zhao@acme.example"}
 ]'::jsonb,
 '[
   {"date": "2026-01-05", "decision": "Use DVC for model versioning instead of plain S3 paths."},
   {"date": "2026-02-18", "decision": "Run feature-store precompute at 02:00 UTC to avoid overlap with batch jobs."}
 ]'::jsonb,
 'active',
 '2026-01-03 08:30:00+01', '2026-04-10 11:45:00+02'),

(4,
 'project-aurora',
 'Internal codename for a greenfield data-lakehouse initiative. Team Neptune is responsible.',
 '[
   {"name": "Fatima El-Amin", "role": "Data Architect",  "email": "fatima@teamn.example"},
   {"name": "Sam Johansson",  "role": "Platform Lead",   "email": "sam@teamn.example"}
 ]'::jsonb,
 '[
   {"date": "2026-03-01", "decision": "Adopt Iceberg table format over Delta Lake — better Spark + Trino interop."},
   {"date": "2026-03-22", "decision": "Use column-level encryption for PII in the lakehouse from day one."}
 ]'::jsonb,
 'active',
 '2026-03-01 09:00:00+01', '2026-04-12 16:00:00+02'),

(5,
 'devops-infra',
 'Shared infrastructure tooling — Terraform modules, CI/CD templates, internal Helm charts.',
 '[]'::jsonb,
 '[
   {"date": "2025-10-14", "decision": "Standardise on GitHub Actions; migrate remaining Jenkins pipelines by Q1 2026."},
   {"date": "2026-02-05", "decision": "Pin Terraform to ~> 1.7 across all modules — avoid breaking changes from 1.8 provider API."}
 ]'::jsonb,
 'active',
 '2025-10-01 08:00:00+01', '2026-04-05 12:30:00+02');

SELECT setval('public.projects_id_seq', 5);

-- ---------------------------------------------------------------------------
-- CONVERSATIONS
-- ---------------------------------------------------------------------------

INSERT INTO public.conversations
  (id, session_id, project_path, model, git_branch, started_at, ended_at,
   message_count, token_count_in, token_count_out, cost_usd, summary, tags)
VALUES
(1,
 'a1b2c3d4-0001-0001-0001-000000000001',
 '/Users/dev/projects/acme-web',
 'claude-sonnet-4-6',
 'feat/user-auth',
 '2026-03-10 09:15:00+01', '2026-03-10 10:42:00+01',
 18, 14200, 3100, 0.0420,
 'Set up JWT authentication for the ACME web portal. Discussed token refresh strategy and settled on 15-minute access tokens + 7-day refresh tokens stored in httpOnly cookies.',
 ARRAY['auth','jwt','security','acme-web']),

(2,
 'a1b2c3d4-0002-0002-0002-000000000002',
 '/Users/dev/projects/acme-web',
 'claude-sonnet-4-6',
 'feat/dashboard-widgets',
 '2026-03-18 14:00:00+01', '2026-03-18 15:55:00+01',
 22, 18900, 4400, 0.0570,
 'Built reusable chart components using Recharts. Discovered a memoisation bug causing full re-renders on filter change — fixed with useMemo on dataset transformation.',
 ARRAY['react','recharts','performance','acme-web']),

(3,
 'a1b2c3d4-0003-0003-0003-000000000003',
 '/Users/dev/projects/fintech-api',
 'claude-sonnet-4-6',
 'fix/reconciliation-timeout',
 '2026-04-01 10:30:00+02', '2026-04-01 12:15:00+02',
 31, 27600, 6200, 0.0810,
 'Diagnosed and fixed a cascade timeout in the reconciliation endpoint. Root cause: N+1 query inside a loop over payment batches. Added bulk-fetch with IN clause and reduced p99 from 8.2s to 340ms.',
 ARRAY['performance','sql','n+1','fintech-api','bug-fix']),

(4,
 'a1b2c3d4-0004-0004-0004-000000000004',
 '/Users/dev/projects/ml-pipeline',
 'claude-opus-4-5',
 'main',
 '2026-04-08 08:00:00+02', '2026-04-08 09:30:00+02',
 14, 11500, 2800, 0.0590,
 'Reviewed nightly Airflow DAG for feature-store precompute. Added retry logic with exponential back-off after upstream data source started returning occasional 503s.',
 ARRAY['airflow','reliability','ml-pipeline','retry']),

(5,
 'a1b2c3d4-0005-0005-0005-000000000005',
 '/Users/dev/projects/project-aurora',
 'claude-sonnet-4-6',
 'spike/iceberg-poc',
 '2026-04-12 13:00:00+02', '2026-04-12 15:40:00+02',
 27, 22400, 5100, 0.0680,
 'Evaluated Apache Iceberg vs Delta Lake for Team Neptune lakehouse. Iceberg wins on Trino compatibility and schema evolution semantics. Decision logged.',
 ARRAY['lakehouse','iceberg','delta-lake','project-aurora','architecture']);

SELECT setval('public.conversations_id_seq', 5);

-- ---------------------------------------------------------------------------
-- MESSAGES  (5-10 per conversation, illustrative sample)
-- ---------------------------------------------------------------------------

INSERT INTO public.messages
  (id, conversation_id, uuid, role, content, token_count, model, duration_ms, created_at)
VALUES
-- Conversation 1: JWT auth
(1,  1, gen_random_uuid(), 'user',      'I need to add JWT auth to the ACME portal. Where should I start?', 22, NULL, NULL, '2026-03-10 09:15:30+01'),
(2,  1, gen_random_uuid(), 'assistant', 'Let''s start with the token strategy. For a web portal I recommend short-lived access tokens (15 min) and longer-lived refresh tokens (7 days) stored in httpOnly cookies — this avoids XSS token theft while keeping UX smooth.', 64, 'claude-sonnet-4-6', 1240, '2026-03-10 09:16:10+01'),
(3,  1, gen_random_uuid(), 'user',      'Should I store refresh tokens in the database?', 14, NULL, NULL, '2026-03-10 09:18:00+01'),
(4,  1, gen_random_uuid(), 'assistant', 'Yes — persist a hashed version in a `refresh_tokens` table with columns: `token_hash`, `user_id`, `expires_at`, `revoked`. This lets you invalidate tokens server-side on logout or suspicious activity.', 58, 'claude-sonnet-4-6', 980, '2026-03-10 09:18:55+01'),
(5,  1, gen_random_uuid(), 'user',      'What library for FastAPI?', 12, NULL, NULL, '2026-03-10 09:20:00+01'),
(6,  1, gen_random_uuid(), 'assistant', '`python-jose` with the `cryptography` extra for token signing, and `passlib[bcrypt]` for password hashing. Both are well-maintained and have good FastAPI community examples.', 44, 'claude-sonnet-4-6', 870, '2026-03-10 09:20:40+01'),
(7,  1, gen_random_uuid(), 'user',      'Implement the /token and /refresh endpoints.', 11, NULL, NULL, '2026-03-10 09:22:00+01'),
(8,  1, gen_random_uuid(), 'assistant', 'Here are both endpoints with proper error handling, CORS headers, and the httpOnly cookie strategy we discussed…', 320, 'claude-sonnet-4-6', 3800, '2026-03-10 09:23:30+01'),

-- Conversation 2: Dashboard widgets
(9,  2, gen_random_uuid(), 'user',      'My Recharts bar chart re-renders on every filter change even when the data hasn''t changed.', 24, NULL, NULL, '2026-03-18 14:00:20+01'),
(10, 2, gen_random_uuid(), 'assistant', 'Classic memoisation miss. The dataset transformation likely runs inline inside the render function. Wrap it with useMemo and make the dependency array explicit.', 45, 'claude-sonnet-4-6', 1100, '2026-03-18 14:01:05+01'),
(11, 2, gen_random_uuid(), 'user',      'Show me an example.', 7, NULL, NULL, '2026-03-18 14:02:00+01'),
(12, 2, gen_random_uuid(), 'assistant', 'Here''s a before/after showing the useMemo fix — you should see renders drop from O(filters) to O(data-changes)…', 210, 'claude-sonnet-4-6', 2600, '2026-03-18 14:03:10+01'),
(13, 2, gen_random_uuid(), 'user',      'That worked! Now let''s add a line chart that overlays the trend.', 18, NULL, NULL, '2026-03-18 14:10:00+01'),
(14, 2, gen_random_uuid(), 'assistant', 'Use ComposedChart from Recharts — it lets you mix Bar and Line components sharing the same XAxis. Here''s the component…', 280, 'claude-sonnet-4-6', 3100, '2026-03-18 14:11:30+01'),

-- Conversation 3: Reconciliation timeout
(15, 3, gen_random_uuid(), 'user',      'The /reconcile endpoint times out under load. p99 is 8.2 seconds, SLA is 2s.', 25, NULL, NULL, '2026-04-01 10:30:15+02'),
(16, 3, gen_random_uuid(), 'assistant', 'Let''s profile first. Can you share the slow query log or an EXPLAIN ANALYZE output?', 28, 'claude-sonnet-4-6', 900, '2026-04-01 10:31:00+02'),
(17, 3, gen_random_uuid(), 'user',      'Here''s the query plan — looks like a Seq Scan on payments inside a loop.', 22, NULL, NULL, '2026-04-01 10:33:00+02'),
(18, 3, gen_random_uuid(), 'assistant', 'N+1 pattern confirmed. You''re fetching each payment individually inside the loop over batches. Replace with a bulk IN query: SELECT * FROM payments WHERE batch_id = ANY($1) — then group in Python. Should cut query count from N to 1.', 72, 'claude-sonnet-4-6', 1450, '2026-04-01 10:34:20+02'),
(19, 3, gen_random_uuid(), 'user',      'Done — p99 dropped to 340ms. Should I add an index too?', 18, NULL, NULL, '2026-04-01 11:15:00+02'),
(20, 3, gen_random_uuid(), 'assistant', 'Yes — CREATE INDEX CONCURRENTLY idx_payments_batch_id ON payments(batch_id). CONCURRENTLY avoids a full table lock in production.', 40, 'claude-sonnet-4-6', 850, '2026-04-01 11:16:00+02'),

-- Conversation 4: Airflow retry
(21, 4, gen_random_uuid(), 'user',      'The feature-store precompute DAG fails randomly with 503 from the upstream data API.', 23, NULL, NULL, '2026-04-08 08:00:30+02'),
(22, 4, gen_random_uuid(), 'assistant', 'Add retry with exponential back-off to the sensor task. Use Airflow''s `retries` + `retry_delay` with `retry_exponential_backoff=True`.', 46, 'claude-opus-4-5', 1350, '2026-04-08 08:01:30+02'),
(23, 4, gen_random_uuid(), 'user',      'Show the DAG config snippet.', 9, NULL, NULL, '2026-04-08 08:03:00+02'),
(24, 4, gen_random_uuid(), 'assistant', 'Here''s the updated task definition with max 5 retries, starting at 30s and doubling each time…', 180, 'claude-opus-4-5', 2200, '2026-04-08 08:04:10+02'),
(25, 4, gen_random_uuid(), 'user',      'Also add a Slack alert if it still fails after all retries.', 16, NULL, NULL, '2026-04-08 08:20:00+02'),
(26, 4, gen_random_uuid(), 'assistant', 'Add an `on_failure_callback` using Airflow''s SlackWebhookHook. I''ll generate the callback function and the connection setup…', 195, 'claude-opus-4-5', 2800, '2026-04-08 08:21:30+02'),

-- Conversation 5: Iceberg spike
(27, 5, gen_random_uuid(), 'user',      'We need to choose between Iceberg and Delta Lake for the new lakehouse. Can you compare them?', 25, NULL, NULL, '2026-04-12 13:00:20+02'),
(28, 5, gen_random_uuid(), 'assistant', 'Key differences: Iceberg has better open-standard interop (works natively with Trino, Spark, Flink); Delta Lake is tighter with Databricks tooling. For your Trino-heavy stack, Iceberg is the safer bet.', 62, 'claude-sonnet-4-6', 1500, '2026-04-12 13:01:30+02'),
(29, 5, gen_random_uuid(), 'user',      'What about schema evolution and time travel?', 14, NULL, NULL, '2026-04-12 13:05:00+02'),
(30, 5, gen_random_uuid(), 'assistant', 'Both support both features. Iceberg''s schema evolution is more flexible — you can rename columns and add nested fields without full table rewrites. Time travel syntax is almost identical.', 54, 'claude-sonnet-4-6', 1100, '2026-04-12 13:06:00+02'),
(31, 5, gen_random_uuid(), 'user',      'OK, we''re going with Iceberg. Log this decision.', 13, NULL, NULL, '2026-04-12 14:00:00+02'),
(32, 5, gen_random_uuid(), 'assistant', 'Decision logged: "Adopt Apache Iceberg over Delta Lake for project-aurora lakehouse. Primary reasons: Trino compatibility and open-standard schema evolution semantics."', 48, 'claude-sonnet-4-6', 900, '2026-04-12 14:00:50+02');

SELECT setval('public.messages_id_seq', 32);

-- ---------------------------------------------------------------------------
-- MEMORY CHUNKS  (18 entries, all 8 categories covered)
-- ---------------------------------------------------------------------------

INSERT INTO public.memory_chunks
  (id, source_type, source_id, content, category, tags, confidence, project_name,
   expires_at, created_at, status, access_count, last_accessed)
VALUES

-- decision (4)
(1,  'conversation', 1,
 'JWT auth strategy for acme-web: 15-minute access tokens + 7-day refresh tokens stored in httpOnly cookies. Refresh tokens persisted as bcrypt hashes in DB with revocation support.',
 'decision', ARRAY['auth','jwt','security','cookies'], 0.97, 'acme-web',
 NULL, '2026-03-10 10:45:00+01', 'active', 12, '2026-04-15 09:30:00+02'),

(2,  'conversation', 3,
 'Reconciliation endpoint fixed by replacing N+1 individual-payment queries with bulk ANY($1) fetch. p99 latency dropped from 8.2s to 340ms. Added CONCURRENTLY index on payments.batch_id.',
 'decision', ARRAY['sql','performance','index','fintech-api'], 0.99, 'fintech-api',
 NULL, '2026-04-01 12:20:00+02', 'active', 7, '2026-04-14 11:00:00+02'),

(3,  'conversation', 5,
 'project-aurora will use Apache Iceberg over Delta Lake. Rationale: native Trino interop and more flexible schema evolution (rename columns, add nested fields without table rewrites).',
 'decision', ARRAY['lakehouse','iceberg','architecture','project-aurora'], 0.98, 'project-aurora',
 NULL, '2026-04-12 14:05:00+02', 'active', 4, '2026-04-13 10:00:00+02'),

(4,  'manual', NULL,
 'Standardise on GitHub Actions for all CI/CD. Remaining Jenkins pipelines must be migrated by end of Q1 2026. Terraform pinned to ~> 1.7 to avoid provider API breakage in 1.8.',
 'decision', ARRAY['ci-cd','github-actions','terraform','devops-infra'], 0.95, 'devops-infra',
 NULL, '2026-02-05 09:00:00+01', 'active', 9, '2026-04-10 14:00:00+02'),

-- pattern (3)
(5,  'conversation', 3,
 'N+1 query pattern in batch endpoints: always bulk-fetch with IN / ANY($1) rather than looping individual queries. Consistent 10-50x speedup observed across fintech-api and acme-web.',
 'pattern', ARRAY['sql','performance','n+1','backend'], 0.95, NULL,
 NULL, '2026-04-01 12:30:00+02', 'active', 18, '2026-04-17 08:30:00+02'),

(6,  'conversation', 2,
 'React memoisation pattern: always wrap dataset transformations in useMemo with explicit dependency arrays when rendering data-heavy charts. Prevents O(filters) re-render cascades.',
 'pattern', ARRAY['react','performance','memoisation','frontend'], 0.90, 'acme-web',
 NULL, '2026-03-18 16:00:00+01', 'active', 11, '2026-04-16 15:00:00+02'),

(7,  'conversation', 4,
 'Airflow resilience pattern: all tasks hitting external APIs should have `retries=5`, `retry_exponential_backoff=True`, `retry_delay=timedelta(seconds=30)`, plus an `on_failure_callback` to Slack.',
 'pattern', ARRAY['airflow','reliability','retry','ml-pipeline'], 0.92, 'ml-pipeline',
 NULL, '2026-04-08 09:35:00+02', 'active', 6, '2026-04-15 07:00:00+02'),

-- insight (2)
(8,  'conversation', 1,
 'Storing refresh tokens as plain JWTs in localStorage is a common XSS attack vector. httpOnly cookie storage eliminates this even when there is an XSS vulnerability, because JS cannot read httpOnly cookies.',
 'insight', ARRAY['security','jwt','xss','auth'], 0.93, NULL,
 NULL, '2026-03-10 10:50:00+01', 'active', 14, '2026-04-12 10:00:00+02'),

(9,  'conversation', 5,
 'Apache Iceberg stores table metadata as immutable JSON snapshot files in object storage — this makes time travel essentially free (no CDC log replay) but does require periodic metadata compaction for large tables.',
 'insight', ARRAY['iceberg','lakehouse','metadata','time-travel'], 0.88, 'project-aurora',
 NULL, '2026-04-12 14:10:00+02', 'active', 3, '2026-04-13 09:00:00+02'),

-- preference (2)
(10, 'manual', NULL,
 'Prefer verbose, self-documenting SQL over ORM-generated queries for complex joins — easier to EXPLAIN ANALYZE and optimise. Use SQLAlchemy Core (not ORM) for performance-critical paths.',
 'preference', ARRAY['sql','sqlalchemy','backend','style'], 0.85, NULL,
 NULL, '2026-01-15 10:00:00+01', 'active', 8, '2026-04-14 09:00:00+02'),

(11, 'manual', NULL,
 'All Python projects: use ruff for linting (replaces flake8+isort), black for formatting, mypy in strict mode. CI must fail on type errors. Pin tool versions in pyproject.toml.',
 'preference', ARRAY['python','tooling','style','ci'], 0.91, NULL,
 NULL, '2026-02-20 11:00:00+01', 'active', 5, '2026-04-10 10:00:00+02'),

-- contact (2)
(12, 'conversation', 1,
 'Jane Smith (jane.smith@acme.example) is the Product Owner for acme-web. Very detail-oriented on UX specs — always include screenshots or Figma links when discussing UI changes.',
 'contact', ARRAY['acme-web','stakeholder','product'], 0.88, 'acme-web',
 NULL, '2026-03-10 10:55:00+01', 'active', 6, '2026-04-08 09:00:00+02'),

(13, 'conversation', 3,
 'Priya Nair (priya.nair@widget.example) is the Security Auditor for fintech-api. Requires written justification for any change to auth or encryption logic before merging.',
 'contact', ARRAY['fintech-api','security','stakeholder'], 0.90, 'fintech-api',
 NULL, '2026-04-01 12:25:00+02', 'active', 4, '2026-04-14 10:00:00+02'),

-- error_solution (2)
(14, 'conversation', 3,
 'Error: psycopg2.OperationalError: SSL connection has been closed unexpectedly — caused by long-running transactions holding idle connections past the server-side timeout. Fix: set keepalives_idle=30 in the connection pool config.',
 'error_solution', ARRAY['postgresql','ssl','connection-pool','fintech-api'], 0.94, 'fintech-api',
 NULL, '2026-04-01 12:35:00+02', 'active', 9, '2026-04-17 11:00:00+02'),

(15, 'conversation', 4,
 'Airflow: "Task exited with return code Negsignal.SIGKILL" on feature-store tasks — root cause was OOM on worker node. Fix: reduce batch_size in DuckDB ingest step from 500k to 50k rows; add memory profiling to CI.',
 'error_solution', ARRAY['airflow','oom','memory','ml-pipeline'], 0.91, 'ml-pipeline',
 NULL, '2026-04-08 09:40:00+02', 'active', 5, '2026-04-16 08:00:00+02'),

-- project_context (2)
(16, 'conversation', 5,
 'project-aurora (Team Neptune): greenfield data lakehouse. Stack: Apache Iceberg on MinIO (S3-compatible), Apache Spark 3.5 for batch, Trino for ad-hoc queries, dbt for transformations. Target GA: Q3 2026.',
 'project_context', ARRAY['project-aurora','lakehouse','architecture','stack'], 0.96, 'project-aurora',
 NULL, '2026-04-12 14:15:00+02', 'active', 7, '2026-04-15 13:00:00+02'),

(17, 'conversation', 2,
 'acme-web frontend stack: React 18 + Vite 5, TypeScript strict, Recharts for data visualisation, React Query for server state, Tailwind CSS. Auth handled via httpOnly cookies with JWT.',
 'project_context', ARRAY['acme-web','react','stack','frontend'], 0.95, 'acme-web',
 NULL, '2026-03-18 16:05:00+01', 'active', 10, '2026-04-16 14:00:00+02'),

-- workflow (1)
(18, 'manual', NULL,
 'New feature workflow: (1) create feature branch from main, (2) open draft PR immediately, (3) self-review with Claude Code before requesting human review, (4) squash-merge with conventional commit message, (5) tag release if API surface changes.',
 'workflow', ARRAY['git','workflow','process','development'], 0.87, NULL,
 NULL, '2026-01-20 09:00:00+01', 'active', 15, '2026-04-17 09:00:00+02');

SELECT setval('public.memory_chunks_id_seq', 18);

-- ---------------------------------------------------------------------------
-- SKILLS
-- ---------------------------------------------------------------------------

INSERT INTO public.skills
  (id, name, version, description, path, triggers, last_used, use_count, created_at, updated_at)
VALUES
(1,
 'project-context-loader',
 '1.2.0',
 'Loads relevant memory chunks, decisions and contacts for the current project at session start.',
 '~/.claude/skills/project-context-loader/SKILL.md',
 ARRAY['load context', 'what do I know about', 'project context', 'start session'],
 '2026-04-17 09:05:00+02', 47,
 '2026-01-15 10:00:00+01', '2026-04-01 09:00:00+02'),

(2,
 'sql-query-builder',
 '1.0.3',
 'Generates optimised PostgreSQL queries from natural language. Adds EXPLAIN ANALYZE, suggests indexes.',
 '~/.claude/skills/sql-query-builder/SKILL.md',
 ARRAY['write a query', 'sql for', 'find all', 'aggregate', 'join', 'slow query'],
 '2026-04-16 14:30:00+02', 31,
 '2026-02-01 11:00:00+01', '2026-03-20 10:00:00+01'),

(3,
 'pr-reviewer',
 '2.1.0',
 'Reviews open PRs: checks for N+1 queries, missing error handling, security anti-patterns, and test coverage gaps.',
 '~/.claude/skills/pr-reviewer/SKILL.md',
 ARRAY['review pr', 'review pull request', 'check my changes', 'review this'],
 '2026-04-17 11:00:00+02', 89,
 '2025-11-10 09:00:00+01', '2026-04-10 12:00:00+02'),

(4,
 'daily-standup-drafter',
 '1.1.0',
 'Reads yesterday''s git commits and memory chunks to draft a standup update (done / doing / blockers).',
 '~/.claude/skills/daily-standup-drafter/SKILL.md',
 ARRAY['standup', 'draft standup', 'what did I do yesterday', 'daily update'],
 '2026-04-17 08:45:00+02', 62,
 '2026-01-05 08:00:00+01', '2026-04-05 09:00:00+02'),

(5,
 'incident-responder',
 '1.0.1',
 'Guides through incident triage: gathers logs, identifies blast radius, drafts status update, writes post-mortem template.',
 '~/.claude/skills/incident-responder/SKILL.md',
 ARRAY['incident', 'outage', 'page', 'on-call', 'production issue'],
 '2026-04-03 02:15:00+02', 8,
 '2026-03-01 14:00:00+01', '2026-04-03 03:00:00+02');

SELECT setval('public.skills_id_seq', 5);

-- ---------------------------------------------------------------------------
-- PROMPTS
-- ---------------------------------------------------------------------------

INSERT INTO public.prompts
  (id, name, category, content, variables, source_path, usage_count, tags, created_at, updated_at)
VALUES
(1,
 'CLAUDE.md — Backend Service',
 'system-prompt',
 E'# Project: {{project_name}}\n\n## Stack\n{{stack_description}}\n\n## Conventions\n- All endpoints require explicit error handling with structured JSON errors\n- Use bulk queries; avoid N+1 patterns\n- SQL migrations use Flyway; never edit existing migration files\n- All secrets via environment variables — no hardcoded credentials\n\n## Key contacts\n{{contacts}}\n\n## Recent decisions\n{{decisions}}',
 '[{"name":"project_name","type":"string"},{"name":"stack_description","type":"string"},{"name":"contacts","type":"string"},{"name":"decisions","type":"string"}]'::jsonb,
 '~/.claude/prompts/claude-md-backend.md',
 23, ARRAY['claude-md','backend','template'],
 '2026-01-10 09:00:00+01', '2026-03-01 10:00:00+01'),

(2,
 'Code Review Checklist',
 'review',
 E'Review the following code changes. Check for:\n\n1. **Correctness** — edge cases, off-by-ones, null handling\n2. **Performance** — N+1 queries, missing indexes, unnecessary allocations\n3. **Security** — injection risks, auth bypasses, secret leakage\n4. **Observability** — missing logs, metrics, tracing\n5. **Tests** — happy path AND error paths covered\n\nFor each issue: severity (critical/major/minor), file:line, explanation, suggested fix.\n\nCode:\n```\n{{code}}\n```',
 '[{"name":"code","type":"string"}]'::jsonb,
 '~/.claude/prompts/code-review.md',
 41, ARRAY['review','checklist','quality'],
 '2025-12-01 09:00:00+01', '2026-02-15 11:00:00+01'),

(3,
 'Post-Mortem Template',
 'incident',
 E'# Post-Mortem: {{incident_title}}\n**Date:** {{date}}  |  **Severity:** {{severity}}  |  **Duration:** {{duration}}\n\n## Summary\n{{summary}}\n\n## Timeline\n| Time | Event |\n|------|-------|\n{{timeline_rows}}\n\n## Root Cause\n{{root_cause}}\n\n## Contributing Factors\n{{contributing_factors}}\n\n## Action Items\n| Item | Owner | Due |\n|------|-------|-----|\n{{action_items}}\n\n## What went well\n{{went_well}}',
 '[{"name":"incident_title","type":"string"},{"name":"date","type":"string"},{"name":"severity","type":"string"},{"name":"duration","type":"string"},{"name":"summary","type":"string"},{"name":"timeline_rows","type":"string"},{"name":"root_cause","type":"string"},{"name":"contributing_factors","type":"string"},{"name":"action_items","type":"string"},{"name":"went_well","type":"string"}]'::jsonb,
 '~/.claude/prompts/post-mortem.md',
 7, ARRAY['incident','post-mortem','template'],
 '2026-02-10 09:00:00+01', '2026-03-15 14:00:00+01'),

(4,
 'ADR — Architecture Decision Record',
 'documentation',
 E'# ADR-{{number}}: {{title}}\n\n**Status:** {{status}}\n**Date:** {{date}}\n**Deciders:** {{deciders}}\n\n## Context\n{{context}}\n\n## Decision\n{{decision}}\n\n## Consequences\n### Positive\n{{positive}}\n\n### Negative / Trade-offs\n{{negative}}\n\n## Alternatives considered\n{{alternatives}}',
 '[{"name":"number","type":"string"},{"name":"title","type":"string"},{"name":"status","type":"string"},{"name":"date","type":"string"},{"name":"deciders","type":"string"},{"name":"context","type":"string"},{"name":"decision","type":"string"},{"name":"positive","type":"string"},{"name":"negative","type":"string"},{"name":"alternatives","type":"string"}]'::jsonb,
 '~/.claude/prompts/adr.md',
 14, ARRAY['adr','architecture','documentation'],
 '2026-01-20 09:00:00+01', '2026-03-01 10:00:00+01'),

(5,
 'Sprint Retrospective Facilitator',
 'process',
 E'Facilitate a sprint retrospective for {{team_name}}.\n\nSprint goal: {{sprint_goal}}\nCompleted: {{completed}}\nNot completed: {{not_completed}}\n\nGenerate:\n1. **Went well** — 3-5 bullet points based on completed items\n2. **Improvements** — 3-5 specific, actionable suggestions\n3. **Action items** — max 3, each with a clear owner and "done" criteria\n\nTone: constructive and forward-looking. Avoid blame.',
 '[{"name":"team_name","type":"string"},{"name":"sprint_goal","type":"string"},{"name":"completed","type":"string"},{"name":"not_completed","type":"string"}]'::jsonb,
 '~/.claude/prompts/retro.md',
 19, ARRAY['agile','retro','process','team'],
 '2026-02-01 09:00:00+01', '2026-04-01 10:00:00+02'),

(6,
 'SQL Migration Generator',
 'database',
 E'Generate a PostgreSQL migration script for the following change:\n\n{{change_description}}\n\nRequirements:\n- Use `CREATE INDEX CONCURRENTLY` for new indexes (no table lock)\n- Wrap DDL in a transaction where possible\n- Add a matching rollback script\n- Add a comment explaining why the change is needed\n- Follow Flyway naming: V{{version}}__{{description}}.sql\n\nCurrent table definition:\n```sql\n{{table_ddl}}\n```',
 '[{"name":"change_description","type":"string"},{"name":"version","type":"string"},{"name":"description","type":"string"},{"name":"table_ddl","type":"string"}]'::jsonb,
 '~/.claude/prompts/sql-migration.md',
 11, ARRAY['sql','database','migration','postgresql'],
 '2026-02-15 09:00:00+01', '2026-03-10 11:00:00+01'),

(7,
 'Release Notes Generator',
 'documentation',
 E'Generate release notes for version {{version}} of {{project_name}}.\n\nGit log since last release:\n```\n{{git_log}}\n```\n\nFormat:\n- Group by: Breaking Changes / New Features / Bug Fixes / Internal\n- Each item: one sentence, past tense, links to PR if available\n- Highlight breaking changes with a warning callout\n- End with upgrade instructions if there are breaking changes',
 '[{"name":"version","type":"string"},{"name":"project_name","type":"string"},{"name":"git_log","type":"string"}]'::jsonb,
 '~/.claude/prompts/release-notes.md',
 8, ARRAY['release','documentation','git','changelog'],
 '2026-03-01 09:00:00+01', '2026-04-01 10:00:00+02');

SELECT setval('public.prompts_id_seq', 7);

-- ---------------------------------------------------------------------------
-- ENTITIES  (Knowledge Graph — 12 entities)
-- ---------------------------------------------------------------------------

INSERT INTO public.entities
  (id, entity_type, name, canonical_name, attributes, first_seen, last_seen,
   mention_count, project_name, confidence)
VALUES
-- People
(1,  'person',      'Jane Smith',         'jane-smith',
 '{"role":"Product Owner","email":"jane.smith@acme.example","team":"acme-web"}'::jsonb,
 '2026-03-10 10:45:00+01', '2026-04-15 09:00:00+02', 8,  'acme-web',    0.95),

(2,  'person',      'Bob Chen',           'bob-chen',
 '{"role":"Lead Backend Dev","email":"bob.chen@acme.example","team":"acme-web"}'::jsonb,
 '2026-03-10 10:45:00+01', '2026-04-16 11:00:00+02', 5,  'acme-web',    0.92),

(3,  'person',      'Alex Kim',           'alex-kim',
 '{"role":"Engineering Manager","email":"alex.kim@widget.example","team":"fintech-api"}'::jsonb,
 '2026-04-01 12:20:00+02', '2026-04-14 10:00:00+02', 4,  'fintech-api', 0.90),

(4,  'person',      'Priya Nair',         'priya-nair',
 '{"role":"Security Auditor","email":"priya.nair@widget.example","team":"fintech-api"}'::jsonb,
 '2026-04-01 12:25:00+02', '2026-04-14 10:00:00+02', 6,  'fintech-api', 0.93),

(5,  'person',      'Dan Reyes',          'dan-reyes',
 '{"role":"ML Engineer","email":"dan.reyes@acme.example","team":"ml-pipeline"}'::jsonb,
 '2026-04-08 08:00:00+02', '2026-04-17 08:00:00+02', 3,  'ml-pipeline', 0.88),

-- Organizations
(6,  'organization','ACME Corp',          'acme-corp',
 '{"industry":"SaaS","size":"mid-market","relationship":"client"}'::jsonb,
 '2026-03-10 09:15:00+01', '2026-04-17 09:00:00+02', 22, 'acme-web',    0.99),

(7,  'organization','Widget Industries',  'widget-industries',
 '{"industry":"FinTech","size":"enterprise","relationship":"client"}'::jsonb,
 '2026-04-01 10:30:00+02', '2026-04-17 10:00:00+02', 15, 'fintech-api', 0.98),

-- Technologies
(8,  'technology',  'Apache Iceberg',     'apache-iceberg',
 '{"type":"table-format","version":"1.5","ecosystem":"data-engineering"}'::jsonb,
 '2026-04-12 13:00:00+02', '2026-04-17 09:00:00+02', 11, 'project-aurora', 0.97),

(9,  'technology',  'pgvector',           'pgvector',
 '{"type":"postgresql-extension","use":"semantic-search","dimension":1536}'::jsonb,
 '2026-01-03 09:00:00+01', '2026-04-16 14:00:00+02', 18, NULL,          0.99),

(10, 'technology',  'Apache Airflow',     'apache-airflow',
 '{"type":"workflow-orchestrator","version":"2.8","use":"nightly-batch"}'::jsonb,
 '2026-04-08 08:00:00+02', '2026-04-17 08:30:00+02', 9,  'ml-pipeline', 0.96),

-- Concepts / Systems
(11, 'system',      'fintech-api reconciliation endpoint', 'fintech-api-reconcile',
 '{"path":"/reconcile","method":"POST","sla_ms":2000}'::jsonb,
 '2026-04-01 10:30:00+02', '2026-04-14 11:00:00+02', 7,  'fintech-api', 0.94),

(12, 'concept',     'N+1 Query Pattern',  'n-plus-one-query',
 '{"category":"anti-pattern","impact":"high","fix":"bulk-fetch"}'::jsonb,
 '2026-04-01 10:34:00+02', '2026-04-17 08:30:00+02', 12, NULL,          0.99);

SELECT setval('public.entities_id_seq', 12);

-- ---------------------------------------------------------------------------
-- RELATIONSHIPS  (8 relationships)
-- ---------------------------------------------------------------------------

INSERT INTO public.relationships
  (id, from_entity, to_entity, relation_type, confidence, source_type, source_id, attributes, created_at)
VALUES
(1,  1, 6,  'works_for',         0.98, 'conversation', 1,
 '{"since":"2025-09-01"}'::jsonb,                          '2026-03-10 10:45:00+01'),

(2,  2, 6,  'works_for',         0.95, 'conversation', 1,
 '{"since":"2025-06-15"}'::jsonb,                          '2026-03-10 10:45:00+01'),

(3,  4, 7,  'works_for',         0.95, 'conversation', 3,
 '{"since":"2024-11-01"}'::jsonb,                          '2026-04-01 12:25:00+02'),

(4,  3, 7,  'works_for',         0.92, 'conversation', 3,
 '{"since":"2023-03-01"}'::jsonb,                          '2026-04-01 12:20:00+02'),

(5,  11, 12, 'had_anti_pattern', 0.99, 'conversation', 3,
 '{"resolved":true,"fixed_at":"2026-04-01","latency_before_ms":8200,"latency_after_ms":340}'::jsonb,
 '2026-04-01 12:35:00+02'),

(6,  8, 7,  'used_by',           0.97, 'conversation', 5,
 '{"context":"lakehouse table format","decided_over":"delta-lake"}'::jsonb,
 '2026-04-12 14:05:00+02'),

(7,  5, 6,  'works_for',         0.88, 'conversation', 4,
 '{"team":"ml-platform"}'::jsonb,                          '2026-04-08 08:05:00+02'),

(8,  10, 5, 'managed_by',        0.90, 'conversation', 4,
 '{"role":"primary-oncall"}'::jsonb,                       '2026-04-08 08:05:00+02');

SELECT setval('public.relationships_id_seq', 8);

COMMIT;

-- =============================================================================
-- Verification query — run after loading to confirm row counts
-- =============================================================================
-- SELECT
--   'conversations'  AS "table", COUNT(*) AS rows FROM public.conversations  UNION ALL
--   SELECT 'messages',           COUNT(*) FROM public.messages               UNION ALL
--   SELECT 'memory_chunks',      COUNT(*) FROM public.memory_chunks          UNION ALL
--   SELECT 'projects',           COUNT(*) FROM public.projects               UNION ALL
--   SELECT 'skills',             COUNT(*) FROM public.skills                 UNION ALL
--   SELECT 'prompts',            COUNT(*) FROM public.prompts                UNION ALL
--   SELECT 'entities',           COUNT(*) FROM public.entities               UNION ALL
--   SELECT 'relationships',      COUNT(*) FROM public.relationships;
