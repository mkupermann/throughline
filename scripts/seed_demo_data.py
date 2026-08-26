#!/usr/bin/env python3
"""Create and seed a fully FICTIONAL Throughline demo database.

Everything this script writes is invented: project names, paths, dialogue,
memory chunks, teams, tasks and log files. It exists so screenshots, videos
and live demos can be produced without a single byte of the operator's real
conversation history appearing on screen.

What it does, in order:

1. ``CREATE DATABASE`` (default name ``throughline_demo``; ``--reset`` drops
   and re-creates it first). It refuses to operate on a database named
   ``throughline`` — the live database is never a valid target.
2. Applies ``sql/schema.sql`` and then runs the packaged migrations against
   the demo database (same runner as ``scripts/migrate.py``).
3. Truncates and re-seeds every table it owns, so re-running always yields
   the same clean fixture (deterministic RNG, seed 20260826).
4. Writes the on-disk ``.ai-pipeline/<run_id>/`` log directories (SPEC.md,
   executor-N.log, verdict-N.txt) that the PM task drill-down's "show log"
   endpoint reads, under ``--workspace`` (default: a ``throughline-demo-repos``
   directory in the system temp dir).

Usage::

    PGHOST=127.0.0.1 PGPORT=5433 PGUSER=throughline PGPASSWORD=... \\
        python3 scripts/seed_demo_data.py --reset

    # then serve it, without touching the live server:
    PGDATABASE=throughline_demo THROUGHLINE_PORT=8791 python3 -m throughline serve

Environment (standard libpq vars, same as the rest of the project):
``PGHOST``, ``PGPORT``, ``PGUSER``, ``PGPASSWORD`` — plus ``PGADMINDB`` for
the maintenance database used to issue CREATE/DROP DATABASE (default
``postgres``). ``POSTGRES_USER``/``POSTGRES_PASSWORD`` from a Compose
``.env`` are honoured as fallbacks when the ``PG*`` variables are unset.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _bootstrap import use_venv

    use_venv()

import argparse
import datetime as dt
import hashlib
import os
import random
import tempfile
import uuid

_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

import psycopg2
from psycopg2.extras import Json

from throughline.config import load_dotenv, repo_root
from throughline.jobs import migrate as migrate_job

UTC = dt.timezone.utc
NOW = dt.datetime.now(tz=UTC)

#: One fixed seed: the fixture is a stage set, and a stage set should look
#: the same every night the play runs.
RNG = random.Random(20260826)

#: Tables this script owns end to end. Order matters only for readability;
#: the TRUNCATE below is a single statement with CASCADE.
SEEDED_TABLES = [
    "pm_task_events",
    "pm_tasks",
    "pm_assignments",
    "pm_team_roles",
    "pm_project_teams",
    "pm_project_repos",
    "pm_projects",
    "pm_teams",
    "pm_members",
    "pm_roles",
    "pm_ai_providers",
    "entity_mentions",
    "relationships",
    "entities",
    "memory_reflections",
    "memory_chunks",
    "messages",
    "conversations",
    "projects",
    "skills",
    "prompts",
    "ingestion_log",
]


def db_config(dbname: str) -> dict:
    """psycopg2 kwargs from the standard PG* vars, with the Compose-file
    POSTGRES_* names as fallbacks so a plain checkout with only ``.env``
    still connects."""
    return {
        "dbname": dbname,
        "user": os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER") or os.environ.get("USER", "postgres"),
        "password": os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD"),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


# ── Database lifecycle ───────────────────────────────────────────────────────


def ensure_database(dbname: str, reset: bool) -> None:
    admin_db = os.environ.get("PGADMINDB", "postgres")
    conn = psycopg2.connect(**db_config(admin_db))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            if reset:
                print(f"==> Dropping database {dbname!r} (if it exists)")
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                print(f"==> Creating database {dbname!r}")
                cur.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        conn.close()


def apply_schema(conn) -> None:
    """Apply sql/schema.sql if the core table is missing (idempotent)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.conversations') IS NOT NULL")
        if cur.fetchone()[0]:
            print("==> Schema already present, skipping sql/schema.sql")
            return
    schema_path = repo_root() / "sql" / "schema.sql"
    print(f"==> Applying {schema_path}")
    sql = "\n".join(
        line for line in schema_path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("\\")
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        # schema.sql empties the session search_path (pg_dump preamble);
        # every later statement here relies on the default again.
        cur.execute("SET search_path TO public")
    conn.commit()


def run_migrations(conn) -> None:
    print("==> Running packaged migrations")
    rc = migrate_job.cmd_migrate(conn, dry_run=False)
    if rc != 0:
        raise SystemExit(f"migrations failed (exit {rc})")


def truncate_seeded(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(SEEDED_TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()


# ── Fixture vocabulary (all fictional) ───────────────────────────────────────

PATH_ROOT = "C:\\dev"

PROJECTS = [
    ("acme-storefront", "E-commerce storefront: catalog, cart, checkout.", "active"),
    ("atlas-billing", "Usage-based billing service and invoice ledger.", "active"),
    ("northwind-mobile", "React Native companion app for field agents.", "paused"),
    ("helios-analytics", "Event pipeline and self-serve dashboards.", "active"),
    ("orion-docs", "Documentation portal with versioned API reference.", "active"),
    ("sparrow-cli", "Developer CLI for environment scaffolding.", "active"),
]

SOURCE_TOOLS = [
    "claude_code",
    "cursor",
    "codex",
    "vibe",
    "windsurf",
    "cline",
    "zed",
    "hermes",
    "continue",
]

MODELS_BY_TOOL = {
    "claude_code": ["claude-sonnet-4-5", "claude-opus-4-1"],
    "cursor": ["gpt-4o", "claude-sonnet-4-5"],
    "codex": ["o3-mini", "gpt-4o"],
    "vibe": ["qwen2.5-coder:32b", "mistral-large-2"],
    "windsurf": ["claude-sonnet-4-5", "gpt-4o"],
    "cline": ["claude-sonnet-4-5", "deepseek-v3"],
    "zed": ["claude-sonnet-4-5"],
    "hermes": ["gpt-4o", "o3-mini"],
    "continue": ["qwen2.5-coder:32b", "gpt-4o"],
}

BRANCHES = [
    "main",
    "main",
    "develop",
    "feature/search-typo-tolerance",
    "feature/invoice-ledger",
    "feature/offline-sync",
    "fix/cart-rounding",
    "fix/session-timeout",
    "chore/dependency-bumps",
    "feature/export-csv",
]

#: (title, [(role, content), ...]) — short engineering dialogues. {p} is
#: replaced with the project name so the transcript references the project
#: it belongs to.
DIALOGUES = [
    (
        "Fix rounding error in cart totals",
        [
            (
                "user",
                "The cart total in {p} is off by a cent when a 3-for-2 discount applies. Order 4711 shows 39.99 but the line items sum to 40.00.",
            ),
            (
                "assistant",
                "That points at per-line rounding. `computeTotal()` rounds each discounted line to 2 decimals and then sums; the discount split 13.33/13.33/13.34 loses a cent. I'd sum in integer cents and round once at the end.",
            ),
            ("user", "Makes sense. Can you change it and keep the receipt lines stable?"),
            (
                "assistant",
                "Done. `priceInCents` now carries through the pipeline, the receipt renderer formats at the edge, and I added a regression test with the 3-for-2 case plus a property test over random baskets: totals now always equal the sum of rendered lines.",
            ),
            ("user", "Ship it behind the pricing flag and note it in the changelog."),
            (
                "assistant",
                "Added `pricing.integer_cents` to the flag registry, defaulted on for staging, and wrote the changelog entry under Fixed.",
            ),
        ],
    ),
    (
        "Plan the invoice ledger migration",
        [
            (
                "user",
                "We need a plan to move {p} from per-invoice rows to an append-only ledger. Zero downtime, and finance wants a reconciliation report.",
            ),
            (
                "assistant",
                "Proposal: 1) create `ledger_entries` alongside the old table, 2) dual-write behind a flag, 3) backfill in 10k batches with a checksum per batch, 4) nightly reconciliation job diffing both stores, 5) cut reads over once diffs are zero for 7 days.",
            ),
            ("user", "How long does the backfill take at 10k per batch?"),
            (
                "assistant",
                "About 2.1M invoices means ~210 batches; at ~40s per batch with the checksum that's under 3 hours. I'd run it off-peak with a rate limiter reading replica lag.",
            ),
            ("user", "Write the migration skeleton and the reconciliation query."),
            (
                "assistant",
                "Both drafted: `migrations/031_ledger_entries.sql` creates the table plus the partial index on unreconciled rows, and `reports/reconcile.sql` groups by invoice and flags any delta over one cent.",
            ),
        ],
    ),
    (
        "Review PR: product search typo tolerance",
        [
            ("user", "Review the typo-tolerance PR for {p} search. Main worry is latency."),
            (
                "assistant",
                "Read through it. The trigram index is right, but `similarity()` in the WHERE clause on every keystroke will not hit the index — you want `%` with `pg_trgm.similarity_threshold` set per session, which stays indexable. Also the debounce is 80ms; 150ms halves query volume with no perceived lag.",
            ),
            ("user", "Good catch on the operator. Anything on the ranking?"),
            (
                "assistant",
                "Ranking mixes similarity and recency with magic constants. Suggest naming them (`W_SIMILARITY = 0.7`, `W_RECENCY = 0.3`) and adding the tie-break on SKU so results are deterministic — the flaky snapshot test is exactly that nondeterminism.",
            ),
            ("user", "Agreed, request changes with those three points."),
            (
                "assistant",
                "Posted the review: indexable operator, debounce to 150ms, named weights plus deterministic tie-break. Marked the test flake as resolved-by-this.",
            ),
        ],
    ),
    (
        "Debug flaky offline sync test",
        [
            (
                "user",
                "`sync.spec.ts` in {p} fails one run in five on CI with a stale queue assertion. Local runs are green.",
            ),
            (
                "assistant",
                "The test seeds the outbox and immediately asserts drain order. On CI the fake timer install races the queue's `setInterval`. Installing fake timers before module import — or injecting the scheduler — removes the race.",
            ),
            ("user", "Scheduler injection sounds cleaner than import gymnastics."),
            (
                "assistant",
                "Agreed. I extracted a `Scheduler` interface with a real and a manual implementation; the test now advances the manual scheduler explicitly. Ran the suite 200 times in a loop: zero failures.",
            ),
            ("user", "Nice. Does production behavior change at all?"),
            (
                "assistant",
                "No — the default export wires the real scheduler, so runtime code paths are byte-identical. Only the test harness changed.",
            ),
        ],
    ),
    (
        "Add CSV export to dashboards",
        [
            ("user", "Customers keep asking to export {p} dashboard tables as CSV. Smallest useful version?"),
            (
                "assistant",
                "Server-side endpoint that re-runs the table's query with the current filters and streams CSV with a bounded row cap (50k) — client-side export breaks on paginated data. I'd put the cap and the content-disposition filename in one config object.",
            ),
            ("user", "Filters include the date range picker, right?"),
            (
                "assistant",
                "Yes — the endpoint takes the same serialized filter state the table already sends, so range, segment and search all apply. Added a `?tz=` param so timestamps export in the viewer's zone, and a unit test for comma-and-quote escaping.",
            ),
            ("user", "Add it to the three tables on the usage page first."),
            (
                "assistant",
                "Done — export buttons on usage, retention and errors tables, feature-flagged `export.csv`, with a toast linking the download.",
            ),
        ],
    ),
    (
        "Harden session timeout handling",
        [
            (
                "user",
                "Users of {p} lose form input when their session expires mid-edit. Support tickets are piling up.",
            ),
            (
                "assistant",
                "Two-part fix: refresh the token in the background while a tab is active, and when refresh fails, stash the form draft in sessionStorage before redirecting to login, restoring it after re-auth.",
            ),
            ("user", "Any risk stashing drafts? Some forms hold payment details."),
            (
                "assistant",
                "Right — the stash must be allowlist-based. I tagged safe forms with `data-draft-safe`; the interceptor only persists those, never card fields. Drafts expire after 15 minutes and clear on successful submit.",
            ),
            ("user", "Good. Add telemetry so we can see recovered sessions."),
            (
                "assistant",
                "Added `session.recovered` and `draft.restored` events with no payload beyond form id and age bucket. Dashboard tile added next to login failures.",
            ),
        ],
    ),
    (
        "Speed up cold start of the CLI",
        [
            ("user", "{p} takes 1.8s before printing anything. Most of that must be imports."),
            (
                "assistant",
                "Profiled it: 1.1s is eagerly importing the cloud SDKs for all providers at startup. Lazy-importing them inside the provider factory drops cold start to 320ms. The plugin discovery scan adds another 200ms we can cache with a mtime-keyed manifest.",
            ),
            ("user", "Cache invalidation on the manifest worries me."),
            (
                "assistant",
                "The manifest stores each plugin dir's mtime; any mismatch triggers a full rescan, so the worst case is exactly today's behavior. `--no-cache` bypasses it for debugging. Added a startup-time budget test that fails CI over 500ms.",
            ),
            ("user", "Merge it, and mention the flag in the docs."),
            ("assistant", "Merged and documented under Performance. Release notes draft updated for 0.9."),
        ],
    ),
    (
        "Write the API reference page for webhooks",
        [
            (
                "user",
                "The {p} webhook docs are three bullet points. Partners keep asking about retries and signatures.",
            ),
            (
                "assistant",
                "Drafted a full page: delivery semantics (at-least-once, 3 retries with exponential backoff, 24h dead-letter), signature verification with a worked HMAC example in three languages, and a table of all event types with sample payloads.",
            ),
            ("user", "Can the samples be generated from the schema so they don't rot?"),
            (
                "assistant",
                "Yes — the payload samples are now rendered at build time from the JSON Schema fixtures, so a schema change regenerates the docs or fails the build if an example no longer validates.",
            ),
            ("user", "Perfect. Link it from the integrations overview."),
            ("assistant", "Linked, plus a redirect from the old anchor so existing partner bookmarks keep working."),
        ],
    ),
]

MEMORY_CHUNKS = [
    # (category, content, project, confidence, tags)
    (
        "decision",
        "Cart totals are computed in integer cents and rounded once at render time — never per line item. Chosen after the 3-for-2 discount off-by-one-cent bug.",
        "acme-storefront",
        0.95,
        ["pricing", "rounding"],
    ),
    (
        "decision",
        "Invoice history moves to an append-only ledger (ledger_entries); dual-write behind a flag until nightly reconciliation shows zero diffs for 7 days.",
        "atlas-billing",
        0.92,
        ["migration", "ledger"],
    ),
    (
        "decision",
        "Search uses pg_trgm with the % operator and a per-session similarity_threshold — similarity() in WHERE clauses is not indexable.",
        "acme-storefront",
        0.9,
        ["search", "postgres"],
    ),
    (
        "decision",
        "CSV export is server-side with a 50k row cap; client-side export was rejected because tables paginate.",
        "helios-analytics",
        0.88,
        ["export", "api"],
    ),
    (
        "decision",
        "Cloud SDKs are lazy-imported inside the provider factory; eager imports cost 1.1s of CLI cold start.",
        "sparrow-cli",
        0.9,
        ["performance", "startup"],
    ),
    (
        "pattern",
        "Timer-driven queues get a Scheduler interface with a manual test implementation — fake-timer installation order caused every flaky sync test so far.",
        "northwind-mobile",
        0.85,
        ["testing", "concurrency"],
    ),
    (
        "pattern",
        "Batch backfills run in 10k-row batches with a per-batch checksum and a rate limiter that reads replica lag.",
        "atlas-billing",
        0.87,
        ["migration", "backfill"],
    ),
    (
        "pattern",
        "Feature flags default on in staging one release before production; the registry entry links the owning ticket.",
        "acme-storefront",
        0.8,
        ["flags", "release"],
    ),
    (
        "pattern",
        "Docs code samples are generated from schema fixtures at build time, so a schema change regenerates or fails the build.",
        "orion-docs",
        0.86,
        ["docs", "codegen"],
    ),
    (
        "insight",
        "Most checkout drop-offs traced to session expiry during address entry, not payment friction — draft-stash plus silent refresh recovered 60% of them.",
        "acme-storefront",
        0.82,
        ["checkout", "sessions"],
    ),
    (
        "insight",
        "Reconciliation deltas cluster at month boundaries: proration rows written in local time, compared in UTC.",
        "atlas-billing",
        0.84,
        ["timezones", "billing"],
    ),
    (
        "insight",
        "Search latency spikes correlate with autovacuum on the products table; raising the table's autovacuum_vacuum_scale_factor smoothed p99.",
        "acme-storefront",
        0.78,
        ["postgres", "latency"],
    ),
    (
        "insight",
        "Dashboard exports are requested overwhelmingly for the usage table — retention and errors exports are near-zero after launch week.",
        "helios-analytics",
        0.75,
        ["export", "usage"],
    ),
    (
        "preference",
        "Team prefers request-changes reviews to carry at most three concrete points, each with a suggested diff.",
        None,
        0.8,
        ["review", "process"],
    ),
    (
        "preference",
        "Changelog entries are written at merge time by the PR author, grouped Added/Changed/Fixed — release day is too late.",
        None,
        0.82,
        ["changelog", "process"],
    ),
    (
        "preference",
        "Magic constants in ranking or scoring code must be named module-level weights with a comment on units.",
        "acme-storefront",
        0.77,
        ["style", "ranking"],
    ),
    (
        "preference",
        "Property-based tests accompany every money-handling change; example-only tests missed the discount split bug.",
        "acme-storefront",
        0.85,
        ["testing", "money"],
    ),
    (
        "contact",
        "Sam Rivera is the release owner for storefront deploys; loops in finance for anything touching invoice or refund flows.",
        "acme-storefront",
        0.9,
        ["people", "release"],
    ),
    (
        "contact",
        "Priya Natarajan owns the analytics event schema registry and reviews every new event name.",
        "helios-analytics",
        0.88,
        ["people", "schema"],
    ),
    (
        "contact",
        "Jonas Weber (mobile) triages Northwind crash reports Mondays; page him only for spikes over 1%.",
        "northwind-mobile",
        0.8,
        ["people", "oncall"],
    ),
    (
        "error_solution",
        "psycopg2 'SSL SYSCALL error: EOF detected' during long backfills — fixed by disabling the pooler's idle timeout for the migration role, not by retry loops.",
        "atlas-billing",
        0.86,
        ["postgres", "backfill"],
    ),
    (
        "error_solution",
        "React Native build failing with 'duplicate symbol' after the sync branch merge: two copies of the sqlite pod; pin one version in the Podfile and de-dupe.",
        "northwind-mobile",
        0.83,
        ["build", "ios"],
    ),
    (
        "error_solution",
        "CSV downloads truncated behind the CDN — the streaming response needed an explicit Content-Length opt-out and chunked encoding allowed at the edge.",
        "helios-analytics",
        0.8,
        ["cdn", "streaming"],
    ),
    (
        "error_solution",
        "CLI plugin cache went stale when a plugin dir was replaced with equal mtime; cache key now includes dir inode plus entry count.",
        "sparrow-cli",
        0.79,
        ["cache", "cli"],
    ),
    (
        "project_context",
        "acme-storefront serves three storefront brands from one codebase; brand config lives in config/brands/*.yaml and is validated in CI.",
        "acme-storefront",
        0.9,
        ["architecture"],
    ),
    (
        "project_context",
        "atlas-billing invoices ~2.1M accounts monthly; heavy jobs must run off-peak (01:00-05:00 UTC) with replica-lag-aware limits.",
        "atlas-billing",
        0.9,
        ["scale", "operations"],
    ),
    (
        "project_context",
        "orion-docs publishes versioned API references for the last four minor releases; older versions are frozen snapshots.",
        "orion-docs",
        0.85,
        ["docs", "versioning"],
    ),
    (
        "workflow",
        "Deploys: merge to main -> staging auto-deploy -> smoke suite -> manual promote before 16:00 local; no Friday promotes.",
        None,
        0.88,
        ["deploy", "process"],
    ),
    (
        "workflow",
        "Schema changes ship expand/contract: additive migration first, code cutover next release, destructive cleanup one release later.",
        "atlas-billing",
        0.9,
        ["migrations", "process"],
    ),
    (
        "workflow",
        "Weekly triage rotates through the four active repos; stale issues over 90 days close automatically with a reopen label.",
        None,
        0.75,
        ["triage", "process"],
    ),
]

SKILLS = [
    (
        "db-migration-safety",
        "Checklist for expand/contract schema changes with backfill batching.",
        ["migration", "schema change", "backfill"],
    ),
    ("release-checklist", "Pre-promote checks: smoke suite, flags, changelog, rollback plan.", ["release", "deploy"]),
    (
        "api-contract-review",
        "Review OpenAPI diffs for breaking changes and versioning policy.",
        ["api", "openapi", "breaking change"],
    ),
    (
        "pricing-money-math",
        "Integer-cent arithmetic rules and property tests for money code.",
        ["pricing", "rounding", "currency"],
    ),
    ("search-tuning", "pg_trgm indexing, thresholds and ranking weight conventions.", ["search", "pg_trgm", "ranking"]),
    ("flaky-test-triage", "Isolate timer/order/network nondeterminism in test suites.", ["flaky", "test", "ci"]),
    (
        "perf-profiling",
        "CPU and import-time profiling workflow for services and CLIs.",
        ["performance", "profile", "cold start"],
    ),
    ("incident-writeups", "Structure and tone for post-incident reviews.", ["incident", "postmortem"]),
    ("docs-style", "Reference-page structure, generated samples, redirects policy.", ["docs", "style guide"]),
    (
        "csv-streaming-exports",
        "Bounded server-side exports: caps, encodings, CDN interplay.",
        ["export", "csv", "streaming"],
    ),
    ("feature-flag-hygiene", "Flag registry conventions, staging defaults, cleanup deadlines.", ["flags", "cleanup"]),
    ("session-auth-flows", "Token refresh, draft stashing and re-auth UX rules.", ["auth", "session"]),
    ("mobile-crash-triage", "Symbolication, dedupe and paging thresholds for RN crashes.", ["mobile", "crash"]),
    (
        "event-schema-review",
        "Naming, payload budgets and PII checks for analytics events.",
        ["analytics", "schema", "events"],
    ),
    ("dependency-upgrades", "Batched dependency bumps with canary and rollback notes.", ["dependencies", "upgrade"]),
]

PROMPTS = [
    (
        "bug-triage",
        "debugging",
        "Summarize the failure, list the three most likely causes ranked by evidence, and propose the smallest experiment that distinguishes them.",
        ["triage", "debugging"],
    ),
    (
        "pr-review-checklist",
        "review",
        "Review this diff for correctness, naming, test coverage and migration safety. At most three request-changes points, each with a suggested edit.",
        ["review"],
    ),
    (
        "migration-plan",
        "planning",
        "Draft an expand/contract migration plan: schema steps, dual-write window, backfill batching, reconciliation query, rollback.",
        ["migration", "planning"],
    ),
    (
        "release-notes",
        "writing",
        "Turn the merged PR titles since the last tag into grouped release notes (Added/Changed/Fixed), user-facing language only.",
        ["release", "writing"],
    ),
    (
        "perf-hypothesis",
        "debugging",
        "Given this profile, name the top three cost centers, expected win for fixing each, and the measurement that would confirm it.",
        ["performance"],
    ),
    (
        "test-flake-isolator",
        "testing",
        "Propose the minimal harness change that removes nondeterminism (timers, ordering, network) without altering production code paths.",
        ["testing", "flaky"],
    ),
    (
        "api-doc-page",
        "writing",
        "Write a reference page: overview, auth, endpoint table, worked example with curl, error table, retry semantics.",
        ["docs", "api"],
    ),
    (
        "incident-summary",
        "writing",
        "Write a blameless incident summary: impact, timeline, root cause, what stopped it, three follow-ups with owners.",
        ["incident"],
    ),
    (
        "sql-review",
        "review",
        "Check this SQL for index usage, lock scope, and batch size; flag anything that scans or locks more than it must.",
        ["sql", "review"],
    ),
    (
        "standup-digest",
        "workflow",
        "Compress these commit messages and PR comments into a three-line standup update: done, next, blocked.",
        ["workflow"],
    ),
]

ENTITIES = [
    # (entity_type, name, project, attributes)
    ("person", "Sam Rivera", "acme-storefront", {"role": "release owner"}),
    ("person", "Priya Natarajan", "helios-analytics", {"role": "schema registry owner"}),
    ("person", "Jonas Weber", "northwind-mobile", {"role": "mobile on-call"}),
    ("person", "Mel Ortiz", "atlas-billing", {"role": "finance liaison"}),
    ("technology", "PostgreSQL", None, {"kind": "database"}),
    ("technology", "pg_trgm", "acme-storefront", {"kind": "extension"}),
    ("technology", "React Native", "northwind-mobile", {"kind": "framework"}),
    ("technology", "Redis", "helios-analytics", {"kind": "cache"}),
    ("project", "acme-storefront", "acme-storefront", {}),
    ("project", "atlas-billing", "atlas-billing", {}),
    ("project", "helios-analytics", "helios-analytics", {}),
    ("concept", "integer-cent arithmetic", "acme-storefront", {}),
    ("concept", "expand/contract migration", "atlas-billing", {}),
    ("concept", "typo-tolerant search", "acme-storefront", {}),
    ("organization", "Acme Retail Group", "acme-storefront", {"kind": "customer"}),
    ("decision", "append-only invoice ledger", "atlas-billing", {}),
]

RELATIONSHIPS = [
    # (from_name, to_name, relation_type)
    ("Sam Rivera", "acme-storefront", "works_on"),
    ("Priya Natarajan", "helios-analytics", "works_on"),
    ("Jonas Weber", "React Native", "uses"),
    ("Mel Ortiz", "atlas-billing", "works_on"),
    ("acme-storefront", "PostgreSQL", "uses"),
    ("acme-storefront", "pg_trgm", "uses"),
    ("atlas-billing", "PostgreSQL", "uses"),
    ("helios-analytics", "Redis", "uses"),
    ("typo-tolerant search", "pg_trgm", "relates_to"),
    ("integer-cent arithmetic", "acme-storefront", "relates_to"),
    ("append-only invoice ledger", "expand/contract migration", "relates_to"),
    ("Mel Ortiz", "append-only invoice ledger", "decided"),
    ("Acme Retail Group", "acme-storefront", "relates_to"),
]


# ── Host / memory domain ─────────────────────────────────────────────────────


def seed_projects(cur) -> dict[str, int]:
    ids = {}
    for name, description, status in PROJECTS:
        cur.execute(
            """
            INSERT INTO projects (name, description, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (name, description, status, NOW - dt.timedelta(days=70), NOW),
        )
        ids[name] = cur.fetchone()[0]
    return ids


def seed_conversations(cur, n_conversations: int = 40) -> list[int]:
    """Conversations + messages, spread over the last ~8 weeks."""
    conversation_ids: list[int] = []
    project_names = [p[0] for p in PROJECTS]
    for i in range(n_conversations):
        project = project_names[i % len(project_names)]
        tool = SOURCE_TOOLS[i % len(SOURCE_TOOLS)]
        title, dialogue = DIALOGUES[i % len(DIALOGUES)]
        model = RNG.choice(MODELS_BY_TOOL[tool])
        started = NOW - dt.timedelta(days=RNG.uniform(0.2, 55.0), minutes=RNG.randint(0, 600))
        duration_min = RNG.randint(18, 95)
        ended = started + dt.timedelta(minutes=duration_min)
        messages = [(role, content.format(p=project)) for role, content in dialogue]
        token_in = RNG.randint(6_000, 90_000)
        token_out = RNG.randint(2_000, 30_000)
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, model, entrypoint, git_branch,
                 started_at, ended_at, message_count, token_count_in,
                 token_count_out, cost_usd, summary, tags, metadata,
                 source_tool, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s)
            RETURNING id
            """,
            (
                str(uuid.UUID(int=RNG.getrandbits(128), version=4)),
                f"{PATH_ROOT}\\{project}",
                model,
                "cli",
                RNG.choice(BRANCHES),
                started,
                ended,
                len(messages),
                token_in,
                token_out,
                round((token_in * 3 + token_out * 15) / 1_000_000, 4),
                title,
                [],
                Json({"source": "demo-fixture"}),
                tool,
                started,
                ended,
            ),
        )
        conversation_id = cur.fetchone()[0]
        conversation_ids.append(conversation_id)

        step = dt.timedelta(minutes=duration_min / (len(messages) + 1))
        for m_index, (role, content) in enumerate(messages):
            cur.execute(
                """
                INSERT INTO messages
                    (conversation_id, uuid, role, content, token_count,
                     model, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    conversation_id,
                    str(uuid.UUID(int=RNG.getrandbits(128), version=4)),
                    role,
                    content,
                    max(12, len(content) // 4),
                    model if role == "assistant" else None,
                    started + step * (m_index + 1),
                ),
            )
    return conversation_ids


def seed_memory(cur, conversation_ids: list[int]) -> None:
    chunk_ids: list[int] = []
    for i, (category, content, project, confidence, tags) in enumerate(MEMORY_CHUNKS):
        created = NOW - dt.timedelta(days=RNG.uniform(1, 50))
        cur.execute(
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, tags, confidence,
                 project_name, created_at, status, access_count, last_accessed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
            RETURNING id
            """,
            (
                "conversation",
                conversation_ids[i % len(conversation_ids)],
                content,
                category,
                tags,
                confidence,
                project,
                created,
                RNG.randint(0, 14),
                created + dt.timedelta(days=RNG.uniform(0, 4)),
            ),
        )
        chunk_ids.append(cur.fetchone()[0])

    # Curate queues: a few superseded pairs, one merged, two expiring.
    superseded_pairs = [(chunk_ids[7], chunk_ids[2]), (chunk_ids[15], chunk_ids[3]), (chunk_ids[29], chunk_ids[27])]
    for old_id, new_id in superseded_pairs:
        cur.execute(
            """
            UPDATE memory_chunks
            SET status = 'superseded', superseded_by = %s, superseded_at = %s
            WHERE id = %s
            """,
            (new_id, NOW - dt.timedelta(days=RNG.uniform(0.5, 6)), old_id),
        )
    cur.execute(
        """
        UPDATE memory_chunks
        SET status = 'merged', superseded_by = %s, superseded_at = %s
        WHERE id = %s
        """,
        (chunk_ids[6], NOW - dt.timedelta(days=2), chunk_ids[8]),
    )
    cur.execute(
        "UPDATE memory_chunks SET expires_at = %s WHERE id = %s",
        (NOW - dt.timedelta(days=3), chunk_ids[19]),
    )
    cur.execute(
        "UPDATE memory_chunks SET expires_at = %s WHERE id = %s",
        (NOW + dt.timedelta(days=9), chunk_ids[22]),
    )
    # And two low-confidence rows for the low-confidence queue.
    cur.execute("UPDATE memory_chunks SET confidence = 0.42 WHERE id = %s", (chunk_ids[13],))
    cur.execute("UPDATE memory_chunks SET confidence = 0.48 WHERE id = %s", (chunk_ids[23],))

    for reflection_type, affected, action, reasoning, confidence in [
        (
            "dedup",
            [chunk_ids[6], chunk_ids[8]],
            "merged",
            "Both chunks state the batch-backfill pattern; kept the version with the replica-lag limiter detail.",
            0.91,
        ),
        (
            "contradiction",
            [chunk_ids[2], chunk_ids[7]],
            "superseded",
            "The flag-default note predates the indexable-operator decision it conflicts with; newer chunk wins.",
            0.84,
        ),
        (
            "dedup",
            [chunk_ids[3], chunk_ids[15]],
            "superseded",
            "Naming-weights preference is subsumed by the CSV export decision's config-object rule for this repo.",
            0.72,
        ),
        (
            "contradiction",
            [chunk_ids[27], chunk_ids[29]],
            "superseded",
            "Triage-rotation note contradicts the newer expand/contract workflow ownership; superseded the older one.",
            0.7,
        ),
        (
            "dedup",
            [chunk_ids[10], chunk_ids[24]],
            "kept_both",
            "Checkout drop-off insight and brand-config context overlap on sessions but carry distinct facts; kept both.",
            0.66,
        ),
    ]:
        cur.execute(
            """
            INSERT INTO memory_reflections
                (reflection_type, affected_chunks, action_taken, reasoning,
                 confidence, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (reflection_type, affected, action, reasoning, confidence, NOW - dt.timedelta(days=RNG.uniform(0.2, 12))),
        )


def seed_skills_prompts(cur) -> None:
    for name, description, triggers in SKILLS:
        created = NOW - dt.timedelta(days=RNG.uniform(10, 65))
        cur.execute(
            """
            INSERT INTO skills
                (name, version, description, path, triggers, last_used,
                 use_count, created_at, updated_at, file_created, file_modified)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                name,
                f"1.{RNG.randint(0, 4)}.0",
                description,
                f"{PATH_ROOT}\\skills\\{name}\\SKILL.md",
                triggers,
                NOW - dt.timedelta(days=RNG.uniform(0, 9)),
                RNG.randint(1, 40),
                created,
                NOW,
                created,
                created + dt.timedelta(days=RNG.uniform(0, 20)),
            ),
        )
    for name, category, content, tags in PROMPTS:
        cur.execute(
            """
            INSERT INTO prompts
                (name, category, content, variables, source_path, usage_count,
                 tags, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                name,
                category,
                content,
                Json([]),
                f"{PATH_ROOT}\\prompts\\{name}.md",
                RNG.randint(0, 25),
                tags,
                NOW - dt.timedelta(days=RNG.uniform(5, 60)),
                NOW,
            ),
        )


def seed_graph(cur, conversation_ids: list[int]) -> None:
    entity_ids: dict[str, int] = {}
    for entity_type, name, project, attributes in ENTITIES:
        cur.execute(
            """
            INSERT INTO entities
                (entity_type, name, canonical_name, attributes, first_seen,
                 last_seen, mention_count, project_name, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                entity_type,
                name,
                name.lower(),
                Json(attributes),
                NOW - dt.timedelta(days=RNG.uniform(20, 60)),
                NOW - dt.timedelta(days=RNG.uniform(0, 10)),
                RNG.randint(2, 18),
                project,
                round(RNG.uniform(0.7, 0.95), 2),
            ),
        )
        entity_ids[name] = cur.fetchone()[0]

    snippets = [
        "raised in review of the typo-tolerance branch",
        "named as owner in the deploy checklist discussion",
        "referenced while planning the ledger backfill",
        "came up during the flaky-test investigation",
        "cited in the export endpoint design",
    ]
    for i, name in enumerate(entity_ids):
        for _ in range(RNG.randint(1, 2)):
            cur.execute(
                """
                INSERT INTO entity_mentions
                    (entity_id, source_type, source_id, context_snippet, created_at)
                VALUES (%s, 'conversation', %s, %s, %s)
                """,
                (
                    entity_ids[name],
                    conversation_ids[(i * 3) % len(conversation_ids)],
                    f"{name} {RNG.choice(snippets)}",
                    NOW - dt.timedelta(days=RNG.uniform(0, 30)),
                ),
            )

    for from_name, to_name, relation_type in RELATIONSHIPS:
        cur.execute(
            """
            INSERT INTO relationships
                (from_entity, to_entity, relation_type, confidence,
                 source_type, source_id, created_at)
            VALUES (%s, %s, %s, %s, 'conversation', %s, %s)
            """,
            (
                entity_ids[from_name],
                entity_ids[to_name],
                relation_type,
                round(RNG.uniform(0.7, 0.95), 2),
                conversation_ids[RNG.randrange(len(conversation_ids))],
                NOW - dt.timedelta(days=RNG.uniform(1, 30)),
            ),
        )


def seed_ingestion_log(cur) -> None:
    for i, (project, _, _) in enumerate(PROJECTS):
        for j in range(1 + (i % 2)):
            path = f"{PATH_ROOT}\\{project}\\.sessions\\session-{j + 1:03d}.jsonl"
            cur.execute(
                """
                INSERT INTO ingestion_log (file_path, file_hash, ingested_at, record_count)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    path,
                    hashlib.sha256(f"demo:{path}".encode()).hexdigest(),
                    NOW - dt.timedelta(days=RNG.uniform(0, 20)),
                    RNG.randint(40, 400),
                ),
            )


# ── PM domain ────────────────────────────────────────────────────────────────

SPEC_SEARCH = """# Task: Add product search with typo tolerance

## Goal
Shoppers who type "runing shoes" must still find "running shoes". Search on
the storefront should tolerate one to two character mistakes without hurting
latency.

## Requirements
- Use the existing PostgreSQL products table; no new search infrastructure.
- p95 search latency stays under 120 ms at current traffic.
- Results are deterministic for identical queries (stable tie-break).
- Behind the `search.typo_tolerance` feature flag, default off in production.

## Acceptance criteria
1. "runing shoes", "runnig shoes" and "running shoes" return the same top-5.
2. The query plan uses the trigram index (verified in the test suite).
3. The snapshot tests for the search results page pass unchanged.
"""

SPEC_ROUNDING = """# Task: Fix cart total rounding

## Goal
Cart totals must always equal the sum of the rendered line items. Order 4711
shows 39.99 vs 40.00 after a 3-for-2 discount.

## Requirements
- Compute in integer cents, round once at render time.
- Receipt line output stays byte-identical for unaffected orders.

## Acceptance criteria
1. Regression test with the 3-for-2 discount basket passes.
2. Property test over random baskets: total equals sum of lines.
"""

SPEC_CHECKOUT = """# Task: Migrate checkout to new API

## Goal
Move checkout submission from the legacy /v1/orders endpoint to the new
orders-service API, keeping the legacy path as fallback behind a flag.

## Acceptance criteria
1. Checkout happy path uses /v2/orders with idempotency keys.
2. Fallback to /v1 on 5xx, with an alert counter.
3. Contract tests against the recorded fixtures pass.
"""

SPEC_LEDGER = """# Task: Dual-write invoices to the ledger

## Goal
Write every new invoice to both the legacy table and the append-only
ledger_entries table, behind the `billing.ledger_dual_write` flag.

## Acceptance criteria
1. Both stores receive identical amounts for new invoices.
2. Reconciliation query reports zero diffs on the test fixture set.
"""

FAIL_VERDICTS_SEARCH = [
    "The trigram index exists but EXPLAIN shows a sequential scan for the two-word query: similarity() in the WHERE clause is not indexable. Acceptance criterion 2 fails.\nVERDICT: FAIL: query plan does not use the trigram index for multi-word queries.",
    'Index is used now, but "runnig shoes" returns a different top-5 than "running shoes" — ranking ties are broken by insertion order. Criterion 1 fails.\nVERDICT: FAIL: typo variants disagree on the top-5 result set.',
    "Tie-break on SKU added, but the snapshot tests for the results page fail: the new ranking reorders two items on the fixtures. Criterion 3 fails.\nVERDICT: FAIL: search results page snapshots need the ranking weights named and frozen.",
    "Snapshots updated deliberately, but p95 latency in the harness is 210 ms — the per-keystroke query has no debounce and fires on every input event. Requirement violated.\nVERDICT: FAIL: p95 latency 210ms exceeds the 120ms budget.",
    "Latency is at 96 ms with the 150 ms debounce, but the feature flag defaults to on in the production config — requirements say default off.\nVERDICT: FAIL: search.typo_tolerance must default off in production.",
]

PASS_VERDICT_SEARCH = (
    "All three acceptance criteria verified: typo variants agree on the top-5, "
    "EXPLAIN confirms the trigram index with the % operator, snapshots pass, "
    "p95 in the harness is 94 ms and the flag defaults off in production.\n"
    "VERDICT: PASS"
)

FAIL_VERDICTS_ROUNDING = [
    "The regression basket passes but the property test found a counterexample: a 7-item basket with two discounts differs by one cent — one code path still rounds per line.\nVERDICT: FAIL: property test counterexample, per-line rounding remains in the discount splitter.",
    "Totals now agree, but the receipt renderer output changed for unaffected orders (trailing zero formatting). Requirement 2 violated.\nVERDICT: FAIL: receipt lines are not byte-identical for unaffected orders.",
]

FAIL_VERDICT_LEDGER = (
    "Dual write happens, but proration rows land with local-time timestamps in "
    "the ledger while the legacy table stores UTC — the reconciliation fixture "
    "reports 14 diffs at the month boundary.\n"
    "VERDICT: FAIL: timezone mismatch between ledger and legacy proration rows."
)

AIDER_FILES = {
    "search": [
        "src/search/query.ts",
        "src/search/ranking.ts",
        "src/search/debounce.ts",
        "tests/search.spec.ts",
    ],
    "rounding": [
        "src/cart/total.ts",
        "src/cart/discounts.ts",
        "tests/cart_total.spec.ts",
    ],
    "checkout": [
        "src/checkout/submit.ts",
        "src/checkout/fallback.ts",
        "tests/checkout.spec.ts",
    ],
    "ledger": [
        "billing/ledger/writer.py",
        "billing/invoices/models.py",
        "billing/reconcile/report.sql",
        "tests/test_ledger_writer.py",
    ],
}


def _executor_log(file_set: str, iteration: int, tokens_total: int, summary: str) -> str:
    """Aider-style log text whose 'Tokens:' lines sum to *tokens_total* (the
    same numbers pm_watch.extract_aider_tokens will read back)."""
    files = AIDER_FILES[file_set]
    picked = RNG.sample(files, k=min(3, len(files)))
    chunks = []
    remaining = tokens_total
    n_calls = RNG.randint(2, 3)
    for call in range(n_calls):
        part = remaining if call == n_calls - 1 else int(remaining * RNG.uniform(0.3, 0.5))
        remaining -= part
        received = max(120, int(part * RNG.uniform(0.02, 0.06)))
        sent = part - received
        chunks.append(
            f"Model: ollama_chat/qwen3-coder:30b with diff edit format\n"
            f"Repo-map: using 2048 tokens, auto refresh\n"
            f"Added {picked[call % len(picked)]} to the chat.\n\n"
            f"{summary}\n\n"
            f"Applied edit to {picked[call % len(picked)]}\n"
            f"Commit a{RNG.randrange(16**6):06x} iteration {iteration}: {summary.rstrip('.').lower()}\n"
            f"Tokens: {sent / 1000:.1f}k sent, {received} received.\n"
        )
    return "\n".join(chunks)


def write_run_dir(
    workspace: Path,
    project: str,
    run_id: str,
    spec: str,
    iterations: list[tuple[int, str | None]],
    file_set: str,
) -> tuple[str, str]:
    """Create <workspace>/<project>/.ai-pipeline/<run_id>/ with SPEC.md,
    executor-N.log and verdict-N.txt files. *iterations* is a list of
    (tokens_used, verdict_text_or_None). Returns (repo_path, log_dir)."""
    repo_dir = workspace / project
    log_dir = repo_dir / ".ai-pipeline" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "SPEC.md").write_text(spec, encoding="utf-8")
    step_summaries = [
        "Implementing the requested change and updating the affected tests.",
        "Addressing the tester's feedback from the previous iteration.",
        "Refining the edit after the failed verdict; adjusting tests to match.",
    ]
    for n, (tokens, verdict) in enumerate(iterations, start=1):
        summary = step_summaries[min(n - 1, len(step_summaries) - 1)]
        (log_dir / f"executor-{n}.log").write_text(_executor_log(file_set, n, tokens, summary), encoding="utf-8")
        if verdict is not None:
            (log_dir / f"verdict-{n}.txt").write_text(verdict + "\n", encoding="utf-8")
    return str(repo_dir), str(log_dir)


def seed_pm(cur, project_ids: dict[str, int], workspace: Path) -> list[int]:
    """Projects, teams, roles, members, assignments, tasks and events.
    Returns the created pm_tasks ids in creation order."""

    # Providers ---------------------------------------------------------------
    cur.execute(
        """
        INSERT INTO pm_ai_providers (name, provider_type, base_url, api_key, custom_models, enabled)
        VALUES ('OpenAI (Team key)', 'openai', NULL, NULL, %s, true)
        """,
        (Json(["gpt-4o", "o3-mini"]),),
    )
    cur.execute(
        """
        INSERT INTO pm_ai_providers (name, provider_type, base_url, api_key, custom_models, enabled)
        VALUES ('Local Ollama', 'ollama', 'http://127.0.0.1:11434', NULL, %s, true)
        """,
        (Json([]),),
    )

    # Skill ids for the Executor role's skill_refs ---------------------------
    cur.execute("SELECT id FROM skills WHERE name IN ('db-migration-safety', 'pricing-money-math') ORDER BY name")
    executor_skill_ids = [row[0] for row in cur.fetchall()]

    # Roles ------------------------------------------------------------------
    def role(name, description, tool, model, *, skills=None, instructions=None, documents=None, budget=None):
        cur.execute(
            """
            INSERT INTO pm_roles
                (name, description, default_ai_tool, default_ai_model,
                 skill_refs, instructions, document_refs, token_budget)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (name, description, tool, model, skills or [], instructions, Json(documents or []), budget),
        )
        return cur.fetchone()[0]

    analyst_id = role(
        "Analyst",
        "Turns a one-line task into a SPEC.md with acceptance criteria.",
        "claude",
        "claude -p (Standard)",
        instructions="Write the smallest spec that makes the task verifiable: goal, hard requirements, numbered acceptance criteria. Never prescribe the implementation.",
    )
    executor_id = role(
        "Executor",
        "Implements the spec, iterating until the tester passes it.",
        "aider",
        "ollama_chat/qwen3-coder:30b",
        skills=executor_skill_ids,
        instructions="Work in small diffs. Run the affected tests before handing over. When a verdict fails, address the stated reason first — do not refactor beyond the spec.",
        documents=[f"{PATH_ROOT}\\handbooks\\engineering-conventions.md"],
        budget=500_000,
    )
    tester_id = role(
        "Tester",
        "Checks the diff against the acceptance criteria and writes the verdict.",
        "vibe",
        "tester-local",
        instructions="Verify each acceptance criterion explicitly. End every verdict file with VERDICT: PASS or VERDICT: FAIL: <reason>.",
    )
    reviewer_id = role(
        "Reviewer",
        "Human sign-off on the final diff before promote.",
        None,
        None,
        instructions="Review the final diff for scope creep and release-note completeness.",
    )

    # Members ----------------------------------------------------------------
    def member(name, member_type, contact=None):
        cur.execute(
            """
            INSERT INTO pm_members (name, member_type, contact_info)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (name, member_type, Json(contact or {})),
        )
        return cur.fetchone()[0]

    claude_id = member("Claude Code", "agent")
    aider_id = member("Aider + qwen3-coder", "agent")
    vibe_id = member("Vibe Tester", "agent")
    sam_id = member("Sam Rivera", "human", {"email": "sam@example.com"})

    # Teams ------------------------------------------------------------------
    cur.execute(
        "INSERT INTO pm_teams (name, description, token_budget) VALUES (%s, %s, %s) RETURNING id",
        ("Delivery Squad", "Analyst -> Executor -> Tester pipeline for storefront work.", 2_000_000),
    )
    delivery_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO pm_teams (name, description, token_budget) VALUES (%s, %s, %s) RETURNING id",
        ("Migration Crew", "Focused crew for the billing ledger migration.", 800_000),
    )
    migration_id = cur.fetchone()[0]

    # PM projects ------------------------------------------------------------
    def pm_project(name, description, status, budget, created_days_ago):
        cur.execute(
            """
            INSERT INTO pm_projects (name, description, status, token_budget, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (name, description, status, budget, NOW - dt.timedelta(days=created_days_ago), NOW),
        )
        return cur.fetchone()[0]

    acme_pm = pm_project(
        "Acme Storefront Relaunch",
        "Search, cart and checkout workstream for the storefront relaunch.",
        "active",
        5_000_000,
        24,
    )
    atlas_pm = pm_project(
        "Atlas Billing Migration",
        "Ledger migration executed by the virtual migration crew.",
        "active",
        2_000_000,
        18,
    )
    northwind_pm = pm_project(
        "Northwind Mobile",
        "Companion app maintenance — wrapped up last quarter.",
        "archived",
        None,
        55,
    )

    # Links ------------------------------------------------------------------
    for pm_id, repo in [
        (acme_pm, "acme-storefront"),
        (atlas_pm, "atlas-billing"),
        (northwind_pm, "northwind-mobile"),
    ]:
        cur.execute(
            "INSERT INTO pm_project_repos (pm_project_id, project_id) VALUES (%s, %s)",
            (pm_id, project_ids[repo]),
        )
    for pm_id, team_id in [(acme_pm, delivery_id), (atlas_pm, migration_id)]:
        cur.execute(
            "INSERT INTO pm_project_teams (pm_project_id, team_id) VALUES (%s, %s)",
            (pm_id, team_id),
        )
    for team_id, role_ids in [
        (delivery_id, [analyst_id, executor_id, tester_id, reviewer_id]),
        (migration_id, [analyst_id, executor_id, tester_id]),
    ]:
        for role_id in role_ids:
            cur.execute(
                "INSERT INTO pm_team_roles (team_id, role_id) VALUES (%s, %s)",
                (team_id, role_id),
            )

    # Assignments (fully staffed pipelines) ----------------------------------
    assignments: dict[tuple[int, int], int] = {}

    def assign(pm_id, team_id, role_id, member_id):
        cur.execute(
            """
            INSERT INTO pm_assignments (pm_project_id, team_id, role_id, member_id)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (pm_id, team_id, role_id, member_id),
        )
        assignments[(team_id, role_id)] = cur.fetchone()[0]

    assign(acme_pm, delivery_id, analyst_id, claude_id)
    assign(acme_pm, delivery_id, executor_id, aider_id)
    assign(acme_pm, delivery_id, tester_id, vibe_id)
    assign(acme_pm, delivery_id, reviewer_id, sam_id)
    assign(atlas_pm, migration_id, analyst_id, claude_id)
    assign(atlas_pm, migration_id, executor_id, aider_id)
    assign(atlas_pm, migration_id, tester_id, vibe_id)

    # Tasks + events + on-disk run dirs --------------------------------------
    def task(pm_id, team_id, title, status, run_id, repo_path, log_dir, tokens_used, started_at, ended_at):
        cur.execute(
            """
            INSERT INTO pm_tasks
                (pm_project_id, team_id, title, status, run_id, repo_path,
                 log_dir, pid, tokens_used, created_at, started_at, ended_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s)
            RETURNING id
            """,
            (pm_id, team_id, title, status, run_id, repo_path, log_dir, tokens_used, started_at, started_at, ended_at),
        )
        return cur.fetchone()[0]

    # Executor 'started' events deliberately carry NO iteration number: the
    # watcher's token-refresh UPDATE (jobs/pm_watch.py) matches on
    # (task_id, step, iteration) without an event_type filter, so a numbered
    # 'started' row would receive the same token count as its 'log_update'
    # twin and double the recomputed task total. The timeline UI only reads
    # 'log_update' and 'verdict' rows per iteration, so nothing is lost.
    def event(
        task_id,
        step,
        event_type,
        *,
        assignment=None,
        iteration=None,
        message=None,
        detail_path=None,
        tokens=None,
        at=None,
    ):
        cur.execute(
            """
            INSERT INTO pm_task_events
                (task_id, assignment_id, step, iteration, event_type, message,
                 detail_path, tokens_used, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, assignment, step, iteration, event_type, message, detail_path, tokens, at or NOW),
        )

    task_ids: list[int] = []

    # 1) Passed task: 6 iterations, 5 FAILs then a PASS. ---------------------
    run_id = "20260820-101500"
    search_tokens = [22_400, 25_100, 27_900, 30_200, 34_300, 41_500]  # ~181k
    search_verdicts = FAIL_VERDICTS_SEARCH + [PASS_VERDICT_SEARCH]
    repo_path, log_dir = write_run_dir(
        workspace,
        "acme-storefront",
        run_id,
        SPEC_SEARCH,
        list(zip(search_tokens, search_verdicts, strict=True)),
        "search",
    )
    started = NOW - dt.timedelta(days=6, hours=2)
    ended = started + dt.timedelta(minutes=52)
    t1 = task(
        acme_pm,
        delivery_id,
        "Add product search with typo tolerance",
        "pass",
        run_id,
        repo_path,
        log_dir,
        sum(search_tokens),
        started,
        ended,
    )
    task_ids.append(t1)
    at = started
    event(
        t1,
        "analyst",
        "started",
        assignment=assignments[(delivery_id, analyst_id)],
        message=SPEC_SEARCH,
        detail_path=str(Path(log_dir) / "SPEC.md"),
        at=at,
    )
    for n, (tokens, verdict) in enumerate(zip(search_tokens, search_verdicts, strict=True), start=1):
        at += dt.timedelta(minutes=2)
        event(
            t1,
            "executor",
            "started",
            assignment=assignments[(delivery_id, executor_id)],
            message=f"Iteration {n} started",
            at=at,
        )
        at += dt.timedelta(minutes=4)
        event(
            t1,
            "executor",
            "log_update",
            assignment=assignments[(delivery_id, executor_id)],
            iteration=n,
            tokens=tokens,
            detail_path=str(Path(log_dir) / f"executor-{n}.log"),
            at=at,
        )
        at += dt.timedelta(minutes=2)
        event(
            t1,
            "tester",
            "verdict",
            assignment=assignments[(delivery_id, tester_id)],
            iteration=n,
            message=verdict,
            detail_path=str(Path(log_dir) / f"verdict-{n}.txt"),
            at=at,
        )

    # 2) Running task: 3 iterations so far, latest without a verdict. --------
    run_id = "20260826-084500"
    rounding_tokens = [15_600, 18_200, 21_300]
    rounding_verdicts = FAIL_VERDICTS_ROUNDING + [None]
    repo_path, log_dir = write_run_dir(
        workspace,
        "acme-storefront",
        run_id,
        SPEC_ROUNDING,
        list(zip(rounding_tokens, rounding_verdicts, strict=True)),
        "rounding",
    )
    started = NOW - dt.timedelta(minutes=38)
    t2 = task(
        acme_pm,
        delivery_id,
        "Fix cart total rounding",
        "running",
        run_id,
        repo_path,
        log_dir,
        sum(rounding_tokens),
        started,
        None,
    )
    task_ids.append(t2)
    at = started
    event(
        t2,
        "analyst",
        "started",
        assignment=assignments[(delivery_id, analyst_id)],
        message=SPEC_ROUNDING,
        detail_path=str(Path(log_dir) / "SPEC.md"),
        at=at,
    )
    for n, (tokens, verdict) in enumerate(zip(rounding_tokens, rounding_verdicts, strict=True), start=1):
        at += dt.timedelta(minutes=2)
        event(
            t2,
            "executor",
            "started",
            assignment=assignments[(delivery_id, executor_id)],
            message=f"Iteration {n} started",
            at=at,
        )
        at += dt.timedelta(minutes=5)
        event(
            t2,
            "executor",
            "log_update",
            assignment=assignments[(delivery_id, executor_id)],
            iteration=n,
            tokens=tokens,
            detail_path=str(Path(log_dir) / f"executor-{n}.log"),
            at=at,
        )
        if verdict is not None:
            at += dt.timedelta(minutes=2)
            event(
                t2,
                "tester",
                "verdict",
                assignment=assignments[(delivery_id, tester_id)],
                iteration=n,
                message=verdict,
                detail_path=str(Path(log_dir) / f"verdict-{n}.txt"),
                at=at,
            )

    # 3) Budget-exceeded task. ----------------------------------------------
    run_id = "20260822-141200"
    checkout_tokens = [248_000, 262_500]  # > Executor's 500k role budget
    repo_path, log_dir = write_run_dir(
        workspace,
        "acme-storefront",
        run_id,
        SPEC_CHECKOUT,
        [(checkout_tokens[0], None), (checkout_tokens[1], None)],
        "checkout",
    )
    started = NOW - dt.timedelta(days=4, hours=1)
    ended = started + dt.timedelta(minutes=41)
    t3 = task(
        acme_pm,
        delivery_id,
        "Migrate checkout to new API",
        "budget_exceeded",
        run_id,
        repo_path,
        log_dir,
        sum(checkout_tokens),
        started,
        ended,
    )
    task_ids.append(t3)
    at = started
    event(
        t3,
        "analyst",
        "started",
        assignment=assignments[(delivery_id, analyst_id)],
        message=SPEC_CHECKOUT,
        detail_path=str(Path(log_dir) / "SPEC.md"),
        at=at,
    )
    for n, tokens in enumerate(checkout_tokens, start=1):
        at += dt.timedelta(minutes=3)
        event(
            t3,
            "executor",
            "started",
            assignment=assignments[(delivery_id, executor_id)],
            message=f"Iteration {n} started",
            at=at,
        )
        at += dt.timedelta(minutes=12)
        event(
            t3,
            "executor",
            "log_update",
            assignment=assignments[(delivery_id, executor_id)],
            iteration=n,
            tokens=tokens,
            detail_path=str(Path(log_dir) / f"executor-{n}.log"),
            at=at,
        )
    at += dt.timedelta(minutes=1)
    event(
        t3,
        "executor",
        "error",
        assignment=assignments[(delivery_id, executor_id)],
        message=f"role budget 500000 exceeded (used {sum(checkout_tokens)})",
        at=at,
    )

    # 4) Stopped task for the migration crew. --------------------------------
    run_id = "20260824-093000"
    ledger_tokens = [96_400, 88_100]
    ledger_verdicts = [FAIL_VERDICT_LEDGER, None]
    repo_path, log_dir = write_run_dir(
        workspace,
        "atlas-billing",
        run_id,
        SPEC_LEDGER,
        list(zip(ledger_tokens, ledger_verdicts, strict=True)),
        "ledger",
    )
    started = NOW - dt.timedelta(days=2, hours=3)
    ended = started + dt.timedelta(minutes=33)
    t4 = task(
        atlas_pm,
        migration_id,
        "Dual-write invoices to the ledger",
        "stopped",
        run_id,
        repo_path,
        log_dir,
        sum(ledger_tokens),
        started,
        ended,
    )
    task_ids.append(t4)
    at = started
    event(
        t4,
        "analyst",
        "started",
        assignment=assignments[(migration_id, analyst_id)],
        message=SPEC_LEDGER,
        detail_path=str(Path(log_dir) / "SPEC.md"),
        at=at,
    )
    for n, (tokens, verdict) in enumerate(zip(ledger_tokens, ledger_verdicts, strict=True), start=1):
        at += dt.timedelta(minutes=2)
        event(
            t4,
            "executor",
            "started",
            assignment=assignments[(migration_id, executor_id)],
            message=f"Iteration {n} started",
            at=at,
        )
        at += dt.timedelta(minutes=8)
        event(
            t4,
            "executor",
            "log_update",
            assignment=assignments[(migration_id, executor_id)],
            iteration=n,
            tokens=tokens,
            detail_path=str(Path(log_dir) / f"executor-{n}.log"),
            at=at,
        )
        if verdict is not None:
            at += dt.timedelta(minutes=2)
            event(
                t4,
                "tester",
                "verdict",
                assignment=assignments[(migration_id, tester_id)],
                iteration=n,
                message=verdict,
                detail_path=str(Path(log_dir) / f"verdict-{n}.txt"),
                at=at,
            )

    return task_ids


# ── Entry point ──────────────────────────────────────────────────────────────


def summarize(conn) -> None:
    print("\nSeeded row counts:")
    with conn.cursor() as cur:
        for table in sorted(SEEDED_TABLES):
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 — fixed list
            print(f"  {table:<20} {cur.fetchone()[0]:>5}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dbname",
        default="throughline_demo",
        help="Demo database name (default: throughline_demo).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and re-create the demo database before seeding.",
    )
    parser.add_argument(
        "--workspace",
        default=str(Path(tempfile.gettempdir()) / "throughline-demo-repos"),
        help="Directory for the fictional repos' .ai-pipeline run logs "
        "(default: <system temp>/throughline-demo-repos).",
    )
    args = parser.parse_args(argv)

    load_dotenv()

    if args.dbname == "throughline":
        print(
            "Refusing to seed a database named 'throughline' — that is the live database name. Pick another --dbname.",
            file=sys.stderr,
        )
        return 2

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    ensure_database(args.dbname, args.reset)

    conn = psycopg2.connect(**db_config(args.dbname))
    try:
        apply_schema(conn)
        run_migrations(conn)
        print("==> Truncating previously seeded rows")
        truncate_seeded(conn)

        print("==> Seeding fictional data")
        with conn.cursor() as cur:
            project_ids = seed_projects(cur)
            conversation_ids = seed_conversations(cur)
            seed_memory(cur, conversation_ids)
            seed_skills_prompts(cur)
            seed_graph(cur, conversation_ids)
            seed_ingestion_log(cur)
            task_ids = seed_pm(cur, project_ids, workspace)
        conn.commit()

        summarize(conn)
        print(f"\nRun-log workspace: {workspace}")
        print(f"pm_tasks ids: {task_ids}")
        print(
            "\nServe it (separate port, never the live server):\n"
            f"  PGDATABASE={args.dbname} THROUGHLINE_PORT=8791 "
            "python3 -m throughline serve"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
