# Provider visibility and a working Timeline — design

Status: approved 2026-08-10 · all decisions closed, ready for implementation planning
Supersedes nothing. Follows `docs/UI_REDESIGN_PLAN.md` (Phases 0–5, complete).

---

## 1. The problem, as diagnosed

The rebuilt UI shipped without the dimension the product exists for. Throughline
unifies memory across nine AI CLIs; `conflicts.py` calls cross-tool comparison
"Throughline's signature analysis". The new interface exposed the originating
tool in exactly one place — the conversation detail record. Not as a facet, not
on a result row, not in Curate, not in the Timeline.

Root cause of the omission: Phase 2 took the *old search page* as its
specification. That page searched six tables and never mentioned tools, so the
facet list inherited its blind spot. Parity was checked against pages, not
against what the product is for.

Investigating the user's report — "I can't see the memory of Claude, Vibe and
Hermes" — found four separate defects, the largest of which (§1.4) was only
found while fact-checking this document.

### 1.1 The tool column does not identify tools

`conversations.entrypoint` means two different things depending on who wrote it:

| adapter | writes | meaning |
|---|---|---|
| `claude_code` | `cli`, `sdk-cli` | *how Claude was invoked* — passes Claude's own value through |
| `windsurf`, `codex`, `cursor`, `zed`, `cline` | their own name | which tool |
| `continue` | `continue.dev` | which tool, name not matching the adapter |
| `hermes` | `s["source"] or "hermes"` | whatever the payload said |
| `vibe` | `""` | nothing — the adapter comments *"Not available in Vibe"* |

On the real database (3,058 conversations): `sdk-cli` 2,944 · `cli` 72 ·
`windsurf` 34 · empty 8. Claude Code — 98% of the corpus — is unrecognisable,
and Vibe is indistinguishable from unknown.

Consequence beyond the UI: `conflicts.py` groups by `entrypoint`, so it treats
`cli` and `sdk-cli` as two different tools and has been reporting **false
cross-tool conflicts between Claude Code and itself**.

### 1.2 Hermes and Vibe were never ingested

Not a UI problem and not an adapter bug. Dry-run parsing, no database writes:

| adapter | files on disk | parses to | errors | rows in DB |
|---|---|---|---|---|
| hermes | 32 | 34 conversations, 4,431 messages | 0 | **0** |
| vibe | 15 | 15 conversations, 4,022 messages | 0 | **0** |
| cline | 0 | — | — | 0 |
| windsurf | 34 | 34 | 0 | 34 |

`ingestion_log` records only `claude_code` and `windsurf`. `ingest --all` was
never run. **8,453 messages sit on disk, fully parseable, one command away** —
and nothing in the product ever said so. Not the old GUI, not the new one, not
`doctor`.

`is_present()` currently means "the directory exists", which is why `cline`
reports present while contributing nothing.

### 1.3 The Timeline is not a timeline

It renders `data.items` — the current page of search results, default 30, max
200. The old Calendar loaded every event in a date range across eight sources
with no page limit. The Phase 4 acceptance bar said "parity with the eight
event sources"; sources were verified reachable, range was never checked.

### 1.4 `claude_code.discover()` misses half its files

Found during spec self-review, and the largest defect of the four.

`discover()` iterates project directories and calls `proj.glob("*.jsonl")` —
non-recursive. Under `~/.claude/projects` there are **250** `.jsonl` files:
126 at depth 2, which it sees, and **124 deeper**, which it cannot. The deeper
ones are subagent transcripts:

```
~/.claude/projects/-Users-mkupermann/<session>/subagents/agent-*.jsonl
```

Nobody decided to exclude them; the glob simply does not reach them.

Checking what is actually ingested makes it worse. Matching discovered files
against `conversations.session_id`:

| | files | already in DB | un-ingested |
|---|---|---|---|
| claude_code, discoverable | 126 | **0** | 126 |
| claude_code, subagents (invisible to glob) | 98 | 0 | 98 |
| hermes | 33 | 0 | 33 |
| vibe | 15 | 0 | 15 |
| windsurf | 34 | 34 | 0 |

The 3,016 Claude Code conversations already in the database came from files
that no longer exist at those paths — Claude Code rotates them. So the
un-ingested backlog is not 47 files across two adapters; it is **272 files
across three**, of which 98 cannot be reached at all without fixing the glob.

This also invalidates a naive coverage metric: comparing "files on disk" to
"rows ingested" is not apples-to-apples, because files rotate away while their
conversations persist. The meaningful signal is **discovered files not present
in `ingestion_log`**, which is what §4.3 reports as `pending`.

---

## 2. Decisions

| # | Decision |
|---|---|
| 1 | Diagnose ingestion before designing — done, §1.2 and §1.4 |
| 2 | Surface un-ingested sources prominently; **never auto-ingest** |
| 3 | New `source_tool` column, written at ingest, backfilled |
| 4 | Timeline gets its own date-range query, with per-provider lanes |
| 5 | Provider appears **both** as a global bar and as a Find facet |
| 6 | Subagent transcripts are discovered and counted, but **not ingested** — §9 |

---

## 3. Data model

### 3.1 `conversations.source_tool text`

A new column, distinct from `entrypoint`, which keeps its real meaning (*how* a
tool was invoked). Provider is *which* tool. Conflating them is the root cause
in §1.1.

Indexed (`btree`), nullable — NULL means "genuinely unknown", which is a state
the UI renders rather than hides.

### 3.2 Adapters

Each adapter writes its own `name` into `source_tool`. `claude_code` stops
passing Claude's entrypoint through, `vibe` stops writing `""`, `hermes` stops
trusting a payload field. `entrypoint` is left exactly as it is — it is still
the right place for `cli` vs `sdk-cli`.

### 3.3 Migration `sql/migrations/002_source_tool.sql`

Adds the column and index, then backfills in priority order:

| rule | expected rows |
|---|---|
| `metadata->>'source'` when it matches a known adapter | 34 → `windsurf` |
| `entrypoint IN ('cli','sdk-cli')` | 3,016 → `claude_code` |
| `entrypoint` matches a known adapter name (incl. `continue.dev` → `continue`) | — |
| otherwise | 8 → `NULL` |

The final 8 are left NULL deliberately. They predate any Vibe files on disk;
labelling them `vibe` would be a fabrication that hardens into fact. They
surface as "unattributed".

Idempotent: re-running only fills rows that are still NULL, so it is safe
alongside `applied_migrations` tracking.

### 3.4 Provider inheritance

`memory_chunks` and `messages` inherit provider through their conversation —
`source_id → conversations.source_tool`. No denormalisation; the join already
exists in every query that needs the tool.

### 3.5 `throughline/providers.py`

One registry: the nine names, display labels, and chart colours. Provider
identity is defined once instead of re-derived in the API, the UI and
`conflicts.py` separately.

### 3.6 `conflicts.py`

Switch its grouping from `entrypoint` to `source_tool`. **This will change
conflict counts, probably downward**, because it stops counting Claude Code
against itself. That is a correction, not a regression, and should be stated in
the changelog.

---

## 4. Provider in the interface

### 4.1 One state, two controls

Both the global bar and the Find facet read and write the same URL parameter,
`?provider=claude_code&provider=hermes`. They are two renderings of one state,
not two states to synchronise, which is what removes the disagreement risk in
decision 5.

### 4.2 Provider is app-scope

Nav links and the ⌘K palette carry `provider` across navigation; category,
tags and confidence stay Find-local and do not. The asymmetry is deliberate —
"I am looking at Hermes" should persist from Find to Curate. The bar keeps the
active scope permanently visible so it can never silently filter something the
user has forgotten about.

The bar is **hidden on Console**: raw SQL ignores it, and a scope control that
does not affect what you are seeing is worse than none.

### 4.3 `GET /api/providers`

The single answer to "what exists, what is imported":

```
name         label        on_disk  pending  excluded  ingested  last_run     status
claude_code  Claude Code      224      126        98      3016  2026-06-07   pending
windsurf     Windsurf          34        0         0        34  2026-02-25   ok
hermes       Hermes            33       33         0         0  never        not_ingested
vibe         Vibe              15       15         0         0  never        not_ingested
cline        Cline              0        0         0         0  never        no_data
codex        Codex              —        —         —         —  —            not_installed
(unattributed)                  —        —         —         8  —            unknown
```

`pending` — discovered files with no `ingestion_log` entry that ingestion
*would* process — is the column that matters. `excluded` covers files that are
discovered deliberately but never ingested (§9). `on_disk` alone is misleading because rotated files leave their
conversations behind (§1.4), so `ingested` can legitimately exceed `on_disk`.
`status` derives from `pending`, not from `ingested == 0`.

Three consumers, one source:

- **Provider bar** — a chip per provider with its count; un-ingested get a
  warning dot.
- **Overview** — an attention item: *"Hermes: 32 files on disk, 0 ingested"*
  with an Ingest action.
- **Operate** — the full table, with per-adapter Ingest.

### 4.4 `is_present()` changes meaning

From "the directory exists" to "at least one parseable file was discovered".
This is what makes `cline` report `no_data` instead of `present`.

### 4.4a `claude_code.discover()` becomes recursive — with an exclusion

`proj.glob("*.jsonl")` → `proj.rglob("*.jsonl")`, so the 98 subagent
transcripts become *countable*. They are simultaneously excluded from
ingestion, because ingesting them as they stand destroys parent-session data
(§9). **These two edits are one change and must not be separated** (§9.1).

### 4.5 Disk-scan caching

The scan is cached ~60 s in-process. It walks ~300 files and hashes nothing, so
it is not expensive — but it changes when you ingest, not per request, and
Overview polls while a job runs.

---

## 5. Timeline

### 5.1 Aggregate, not events

```
GET /api/timeline?since&until&bucket=day|week|month&kind&provider
  → [{bucket, provider, kind, n}]
```

90 days × 9 providers is ~810 rows regardless of corpus size. Bucket
auto-selects: ≤90 days by day, ≤2 years by week, beyond by month — so "all
time" stays cheap.

Detail on demand:

```
GET /api/timeline/day/{date}   → that day's events, bounded and paged
```

Clicking a cell is what loads rows.

### 5.2 Lanes use the sequential ramp, not categorical hues

Six validated chart hues exist against nine providers — a real constraint from
Phase 1 (§5.4 of the redesign plan). Each lane is already labelled with its
provider name, so hue would be redundant; intensity is what the cell means.
This sidesteps the palette ceiling and is the more honest encoding. Categorical
colour stays on the provider chips, where a label alone is not enough.

```
              Feb        Mar        Apr        May        Jun
claude_code   ░░         ███        ████       ████       ██
windsurf      ███        ░          ·          ·          ·
hermes        ·          ·          ·          ·          ·     ⚠ not ingested
vibe          ·          ·          ·          ·          ·     ⚠ not ingested
not tool-specific        ░░         ░          ░░         ░
```

### 5.3 Non-provider sources

Skills, projects, prompts, entities and reflections are not per-tool. They get a
final **"not tool-specific"** lane rather than being forced into a provider or
dropped, so all eight of the old Calendar's sources stay reachable.

### 5.4 Timeline becomes its own surface

It stops being a view mode of search results and gains its own range control.
Facets still narrow it and the provider bar scopes it, but it no longer
inherits pagination — which is the actual defect.

---

## 6. Acceptance bars

Each is binary and pre-registered. A miss is not re-scoped afterwards.

| # | Bar |
|---|---|
| 1 | After migration, every conversation has `source_tool` set or explicitly NULL; the 3,016 Claude Code rows report `claude_code`, not `cli`/`sdk-cli` |
| 2 | Re-running the migration changes zero rows |
| 3 | Every one of the nine adapters writes its own name — asserted table-driven, so a new adapter cannot forget |
| 4 | `/api/providers` reports Hermes and Vibe as `not_ingested` with non-zero `pending`, `cline` as `no_data`, and Claude Code as `pending 126 / excluded 98` |
| 4a | After the recursive glob, `ingest --all` still produces exactly one conversation per parent session — no subagent file collapses onto a parent row |
| 5 | The provider scope survives navigation between Overview, Find, Curate and Operate, and bar and facet never disagree |
| 6 | **A date range with no query shows every conversation in that range, and lane totals equal `SELECT count(*)` for the same range — verified against the real database, not the Docker one** |
| 7 | `conflicts.py` no longer reports Claude Code in conflict with itself |

Bar 6 is stated at length because Timeline has now failed twice by verifying the
wrong property: sources reachable rather than range complete, page rendered
rather than data whole.

---

## 7. Testing

- **Migration** — backfill correctness per rule; idempotency on re-run; NULL
  preserved for genuinely unknown rows.
- **Adapters** — table-driven over all nine: each writes its own name.
- **Aggregation** — bucket boundaries at month edges and across DST; lane
  totals reconcile against raw counts.
- **Coverage** — `is_present()` false for an empty directory; `not_ingested`
  surfaces when files exist and rows do not.
- **Frontend (Vitest)** — provider param survives navigation; bar and facet
  derive from one param.
- **Conflicts** — a Claude Code pair with differing `entrypoint` values is not
  reported as cross-tool.
- **Subagent exclusion** — a regression test that ingests a fixture with a
  parent session and three subagent transcripts sharing its `sessionId`, and
  asserts the parent keeps all its messages. This is the test that would have
  caught the hazard in §9 before it reached the database.

---

## 8. Out of scope

- Auto-ingestion on a schedule (decision 2: explicitly rejected).
- Backfilling provider onto `messages` or `memory_chunks` as columns — they
  inherit through the join.
- Guessing a provider for the 8 unattributed rows.
- Any change to `entrypoint` semantics or existing values.

---

## 9. Decision: subagent transcripts are discovered but not ingested

Delegated to the implementer and decided on evidence, 2026-08-10.

**The decision: make discovery recursive so the 98 subagent transcripts are
counted and visible, and exclude them from ingestion in this iteration.**

The reason is not that they are derivative noise — they are substantial (12–16
messages each, averaging 730 KB, *larger* than top-level sessions) and they
contain work that exists nowhere else, because a parent session records only a
subagent's final summary. On value alone they deserve ingestion.

The reason is a demonstrable correctness hazard:

```
98 subagent files collapse to 7 distinct session_ids
   94cc8d5c  <- 33 subagent files
   68c8da3e  <- 18
   92ffcc3c  <- 18
   8c7d7753  <-  9
```

`claude_code` takes `session_id` from the `sessionId` field inside the
transcript, and a subagent inherits its parent's. The parent's own file exists
at depth 2 with the same id. `_upsert_conversation` keys on
`ON CONFLICT (session_id)` and `_replace_messages` does
`DELETE FROM messages WHERE conversation_id = …` before inserting.

So ingesting them as they stand means 33 subagent transcripts **and** the
parent session all resolve to one `conversations` row, each deleting the last
one's messages. Only the final file processed survives. The ingest reports
success. That is silent data loss.

### 9.1 Sequencing constraint — these must land together

`ingest --all` is **safe today** precisely because the glob is non-recursive:
subagent files never reach the writer. Fixing the glob on its own would
*introduce* the bug described above.

Therefore the recursive glob and the ingestion exclusion are a single change,
never two. A plan that ships `rglob` first is wrong.

### 9.2 Coverage reports them as excluded, not pending

`pending` means "ingestion would process this and has not". Subagents would not
be processed, so counting them as pending would cry wolf about 98 files
forever. The provider table gains an `excluded` column with a reason, so they
are visible without being actionable noise:

```
name         label        on_disk  pending  excluded  ingested  status
claude_code  Claude Code      224      126        98      3016  pending
```

### 9.3 Follow-up, specified rather than deferred vaguely

Ingesting subagent transcripts properly requires giving them their own
identity. The concrete change:

- `session_id = uuid5(NAMESPACE_URL, f"{parent_session_id}/{file.stem}")` —
  deterministic, so re-ingestion stays idempotent
- a `parent_session_id` column on `conversations`, so a subagent run can be
  shown under the session that spawned it
- `source_tool` stays `claude_code`; a `subagent` boolean or a tag makes them
  filterable, so memory extraction and `conflicts.py` can exclude them and
  avoid double-counting the same reasoning

That is a separate piece of work with its own acceptance bar. It is not in this
spec.
