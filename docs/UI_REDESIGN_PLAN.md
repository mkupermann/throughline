# Throughline Interface Redesign — Plan

Status: proposal, 2026-08-09
Decision inputs: rebuild on FastAPI + React/Tailwind/shadcn · primary user is one
person running it locally, daily · full product rethink (not a reskin).

---

## 1. Diagnosis

Measured against the current `gui/` tree (4,843 lines: `app.py` 1,871 + 15 page
modules), not asserted.

| Finding | Measurement | Consequence |
|---|---|---|
| Raw HTML injection surface | **97** `unsafe_allow_html=True` call sites, interpolating DB content into f-strings (`page_views/dashboard.py:151` is representative) | Stored XSS. Already an open finding in the memory DB. Cannot be fixed site-by-site at this count. |
| No accessibility layer | **1** `aria-` attribute in 4,843 lines; **0** `focus-visible` rules | Keyboard operation is whatever Streamlit happens to give you. |
| Navigation is flat | 14-item `st.radio`, no grouping | Six entries (Search, Semantic, Conversations, Memory, Memory Health, Knowledge Graph) are all "find something in my memory". |
| Routing is a hack | `?type=&id=`; setting a detail param sets `page = None` and the nav disappears; back = `st.query_params.clear()` | No deep link to a page, no filter/scroll state preservation, browser back is unreliable. |
| Metric wall | **34** `st.metric` calls; Dashboard alone renders 11 tiles in 3 rows, all the same visual weight | No hierarchy — nothing tells you what needs attention. |
| Dashboard duplicates a page | `dashboard.py:52-82` renders the Memory Health card; "Memory Health" is also its own nav entry | Two places to look, neither authoritative. |
| Data palette is not colorblind-safe | See §5.4 — fails 3 of 5 validator checks | `workflow` ↔ `project_context` are ΔE 2.3 under deuteranopia. `error_solution` ↔ `contact` are ΔE 8.1 under *normal* vision. |
| Theming is not themeable | Tokens are Python constants baked into an f-string CSS blob | Dark-only. No light mode possible without editing Python. |
| Runtime network dependency | Inter imported from `rsms.me` inside the CSS | Breaks offline and in air-gapped Docker; FOIT on cold start. |
| Emoji as iconography | `⚠` literals in `memory.py:101,103,113`, `app.py:1204,1700` | Font-dependent, untintable, inconsistent with any icon set. |

**Read of it:** the visual layer is decent — the GitHub-dark surface palette is
coherent and worth keeping. Everything that is wrong is *structural*: information
architecture, routing, the HTML-string component layer, and the input model.
A reskin would fix none of it. That is the case for the rebuild.

---

## 2. What the product actually is

Throughline ingests AI coding sessions from many tools into Postgres, extracts
memory chunks, embeds them, links entities, and serves them back through MCP.

The interface exists for exactly three jobs:

1. **Find** — retrieve something out of the memory base.
2. **Curate** — keep the memory base trustworthy (conflicts, drift, staleness, forgetting).
3. **Operate** — keep the pipeline running (ingest, embed, schedule, diagnose).

Today those three verbs are spread across fourteen nouns. The rethink is to
build the three verbs and let the nouns be *facets*, not pages.

---

## 3. Target information architecture

**Five surfaces, replacing fourteen pages.**

### `/` — Overview

Not a KPI wall. A **worklist**: one headline number (memory chunks under
management), one health verdict (OK / degraded / broken, from
`throughline.status.collect_status`), and then *only the things that need
attention* — outstanding contradictions, embedding gap, stale ingestion, failed
scheduler runs. Each row links straight into Curate or Operate with the filter
pre-applied. 14-day activity sparkline below the fold, not above it.

If nothing needs attention, the page says so in one line. That is a feature.

### `/find` — the unified retrieval surface

One query box. **Hybrid retrieval**: pg_trgm lexical (`idx_memory_content_trgm`,
`idx_messages_content_trgm`) fused with pgvector HNSW semantic (`embeddings`),
combined by reciprocal-rank fusion, over *all* record types at once.

Today `page_views/search.py` already runs six separate ILIKE queries across
conversations, messages, memory_chunks, skills, projects and prompts — it is
already the unified surface, just not presented as one, and not fused with the
vector path that lives in a different page.

Facets (URL-encoded, all of them): record type · category · project · status ·
confidence · date range · tags · has-embedding.

Four **view modes** over the same result set, not four pages:

| Mode | Replaces | Notes |
|---|---|---|
| List | Search, Semantic, Memory, Conversations, Skills, Prompts | Default. Ranked, snippet-highlighted. |
| Table | — | Virtualized, sortable, column-pick, export. |
| Timeline | Calendar | The 8 event sources `calendar.py` already assembles, as a filtered view of the same query. |
| Graph | Knowledge Graph | Force-directed over `entities`/`relationships`, **always a subgraph of the current result set** — never the full graph. |

Absorbs: Search, Semantic, Conversations, Memory (browse), Skills, Prompts,
Knowledge Graph, Calendar, Projects (browse). **Nine pages → one.**

### `/curate` — memory quality workbench

The surface that does not exist today, and is the actual reason this tool has
long-term value. Queues, each with a count badge and a bulk action:

- Contradictions outstanding (`throughline/conflicts.py`)
- Drift-audit hits (`scripts/audit_extraction.py`)
- Superseded chains — `memory_chunks.superseded_by` walked and shown as a chain
- Low confidence (`confidence < threshold`)
- Missing embeddings
- Expiring / expired (`expires_at`)
- Never accessed (`access_count = 0`, aged)

Every mutation goes through `scripts/forget.py` semantics and gets an **undo
toast**. Absorbs: Memory Health + the mutating half of Memory.

### `/operate` — pipeline control

Ingestion runs and `ingestion_log`, scheduler state, embedding backfill,
`throughline doctor` output, DB connection facts. Absorbs: Ingestion, Scheduler,
plus the DB-health strip currently in the sidebar.

### `/console` — SQL

Kept as-is in capability, rebuilt as a proper editor (CodeMirror, schema-aware
completion from `sql/schema.sql`, query history, result export). Power tool,
single user, local — it stays.

### Detail routes

`/c/:id` conversation · `/m/:id` memory chunk · `/e/:id` entity ·
`/p/:name` project · `/s/:id` skill · `/pr/:id` prompt.

Real routes, not query params. The nav stays visible. Browser back works.

---

## 4. Architecture

### 4.1 Extract the query layer first

The single most important structural move, and it is *not* frontend work.

All SQL currently lives inline in `gui/page_views/*.py` as string literals. It
must move to `throughline/queries/` as typed, parameterised functions importable
by the API, the CLI, and the MCP server.

Precedent already exists in the repo: `throughline.status.collect_status` is
shared by CLI, MCP and GUI. Extend that pattern to everything.

This is also the moment to examine the open query findings recorded in the
memory DB, because we are rewriting these call sites anyway:

- ~~`search_semantic` CTEs bypass the HNSW index (full scan)~~ — **measured and
  refuted, 2026-08-09.** See below.
- `conflicts` uses O(n²) self-joins → bound by project + category + time window.
- Writer does one INSERT per message → `execute_values`.

**Do not carry the remaining two into the new API.** Fixing them during
extraction costs little; fixing them after the API has consumers costs a
migration.

#### Correction: the HNSW finding does not reproduce

The recorded finding was taken at face value in the first draft of this plan.
It was then tested on PostgreSQL 16 + pgvector 0.8.2 with 50 000 seeded
embeddings, and it is wrong: PostgreSQL inlines those CTEs and pushes the
ordering down on its own, so the HNSW index *is* used.

The "fix" (per-branch `ORDER BY … LIMIT` pushed onto `embeddings`) was
implemented and measured against the original:

| case | plan | rows returned | time |
|---|---|---|---|
| unfiltered, original | HNSW index scan | 20 | 0.39 ms |
| unfiltered, pushed-down | HNSW index scan | 20 | 0.16 ms |
| **project-filtered, original** | HNSW + iterative filter | **20** | **0.79 ms** |
| **project-filtered, pushed-down** | HNSW capped, then filtered | **2** | 13.9 ms |

The pushdown caps the candidate set *before* the project predicate applies, so
a selective filter silently returns a fraction of the requested rows. It is a
correctness regression bought for no speed, and it was reverted.

What was kept from the attempt: the embedding column is now validated against a
whitelist rather than interpolated into the SQL text, `similar_to_source` reads
its probe vector via a scalar subquery instead of guessing at the Python
representation, and
`tests/integration/test_semantic_queries.py::test_project_filter_still_fills_the_limit`
now guards the truncation bug the optimisation would have introduced.

Method note for the remaining two findings: measure before fixing, and measure
the *filtered* path, not just the happy one. One of three recorded performance
findings has already turned out to be a non-issue.

### 4.2 Backend

```text
throughline/
  api/
    __init__.py        FastAPI app factory
    deps.py            connection pool, settings (reuses throughline/config.py)
    routers/
      overview.py      GET  /api/overview
      find.py          GET  /api/find            (hybrid, faceted, cursor-paged)
                       GET  /api/find/facets
      detail.py        GET  /api/{kind}/{id}
      curate.py        GET  /api/curate/queues
                       POST /api/curate/forget   (idempotency-key + undo token)
                       POST /api/curate/resolve
      operate.py       GET  /api/operate/status
                       POST /api/operate/run/{job}   (SSE progress)
      console.py       POST /api/console/query   (read-only role, statement_timeout)
  queries/             the extracted SQL layer
```

- **Connection pooling**: `psycopg_pool`. The Streamlit app opens ad-hoc
  connections; a long-lived server must not.
- **Long jobs** (ingest, embed, reflect) stream progress over SSE. Today they are
  `subprocess` calls that block a rerun.
- **Console safety**: separate read-only Postgres role +
  `SET LOCAL statement_timeout`. Not a regex blocklist.
- **PII**: `throughline.pii.redact` stays server-side, applied at serialization,
  toggleable per request.
- **Auth**: none. Bind `127.0.0.1` only, and refuse to start on `0.0.0.0` without
  an explicit `THROUGHLINE_ALLOW_REMOTE=1`. Single local user — this is the
  correct trade, but it must be enforced in code, not documentation.

### 4.3 Frontend

```text
web/
  src/
    app/          router, shell, theme provider, command palette
    features/     overview | find | curate | operate | console | detail
    components/   ui/ (shadcn primitives) + charts/ + data/
    lib/          api client (generated from OpenAPI), query keys, url-state
```

| Concern | Choice | Why |
|---|---|---|
| Build | Vite + React 19 + TypeScript | No SSR needed — it is a local single-user app. Next.js would be dead weight. |
| Styling | Tailwind v4 + shadcn/ui | v4's native CSS-variable theming is exactly the token model needed. |
| Server state | TanStack Query | Cache, background refetch, optimistic mutations with rollback. |
| Tables | TanStack Table + TanStack Virtual | 552+ chunks today, unbounded later. Virtualize above 50 rows. |
| Routing | React Router (data router) | URL owns all filter/sort/facet state. |
| Command palette | `cmdk` (shadcn `Command`) | §6. |
| Charts | Recharts, or hand-rolled SVG for the simple forms | §7. |
| Graph | Cytoscape.js | Replaces `streamlit-agraph`. Real layout control, subgraph-first. |
| Calendar | FullCalendar React | Same engine `streamlit-calendar` already wraps — feature parity is reachable. |
| Editor | CodeMirror 6 + `@codemirror/lang-sql` | Console. |
| Icons | `lucide-react`, one family, no emoji | Kills the `⚠` literals. |

**XSS is fixed structurally, not by review.** React escapes by default;
`dangerouslySetInnerHTML` gets an ESLint `no-restricted-syntax` error with no
exemption. That is how 97 injection sites go to zero and stay there.

### 4.4 Serving and packaging

`throughline serve` starts uvicorn; FastAPI mounts the built `web/dist` as static
assets and serves `index.html` for unknown paths (SPA fallback). One process, one
port, one command. Docker gains a Node build stage; the runtime image ships only
the built assets — no Node at runtime.

The API client is generated from the FastAPI OpenAPI schema, so the contract
cannot drift silently.

---

## 5. Design system

### 5.1 Tokens

CSS custom properties on `:root`, overridden under `[data-theme="dark"]` and
`@media (prefers-color-scheme: dark)`. Light mode is built from day one even
though the daily use is dark — with a token system it costs almost nothing, and
retrofitting it later costs a lot.

Keep the current GitHub-dark **surface** ramp. It is good, it is familiar, and it
is not the problem:

```css
--surface-base      #0D1117
--surface-raised    #161B22
--surface-hover     #1C2128
--border            #30363D
--border-muted      #21262D
--text-primary      #C9D1D9
--text-secondary    #8B949E
--text-muted        #6E7681
--accent            #58A6FF
```

Status colours stay reserved (`success/warning/danger`) and are never reused as
chart series colours.

### 5.2 Typography

- UI: system stack (`-apple-system, "SF Pro Text", system-ui`). **Self-hosted
  fallback only — no runtime CDN fetch.** On this Mac the system stack renders
  identically to Inter for practical purposes and costs zero bytes.
- Mono: JetBrains Mono, self-hosted, subset. Tabular figures on all numeric
  columns (`font-variant-numeric: tabular-nums`) so counts stop jittering.
- Scale (density 8/10 — this is a dense tool, not a marketing page):
  `11 / 12 / 13 / 14 / 16 / 20 / 28`, body 14, line-height 1.5.

### 5.3 Spacing / radius

Spacing `4 / 8 / 12 / 16 / 24 / 32`. Radius `4` controls, `8` cards, `12` sheets.
One elevation scale, three steps. No glassmorphism, no blur — it costs frame
budget and buys nothing in a data tool.

### 5.4 Colour for data — the part that is computed, not chosen

The current 8 category colours were validated against the dataviz six-check
validator on the real `#0D1117` surface:

```text
[FAIL] Lightness band      7 of 8 outside L 0.48–0.67 (all too light)
[FAIL] CVD separation      worst #D2A8FF↔#79C0FF ΔE 2.3 (deutan)
[FAIL] Normal-vision floor worst #F85149↔#FF7B72 ΔE 8.1 — indistinguishable
                           even with full colour vision
```

Search over the OKLCH gamut established the hard limit:

| Hue slots | Worst CVD ΔE (all pairs) | Worst normal ΔE | Verdict |
|---|---|---|---|
| 4 | 12.4 | 22.8 | comfortable |
| 5 | 12.3 | 22.0 | comfortable |
| **6** | **9.6** | **17.4** | **passes all five checks** |
| 8 | 7.6 | 14.1 | fails — inside the 6–8 floor band |

**Eight categories cannot be encoded by hue alone.** Therefore two palettes:

**(a) Chart series — 6 validated slots, fixed order, never cycled:**

```text
#9634A6  #D0365A  #B88923  #646506  #00949D  #9678F7
```

```bash
node scripts/validate_palette.js "#9634A6,#D0365A,#B88923,#646506,#00949D,#9678F7" \
     --mode dark --surface "#0D1117" --pairs all
→ ALL CHECKS PASS
```

A 7th and 8th category fold into a neutral **Other** slot, or the chart facets
into small multiples. These hexes are muted by necessity — protan/deutan
separation at constrained lightness is what costs the vividness. **Any
substitution must be re-run through the validator, on both surfaces.** Do not
eyeball it.

**(b) Category chips — all 8 keep a distinct colour**, because a chip always
carries its text label, so identity is never colour-alone. The existing 8 hues
are fine here after a lightness-band correction.

Rules that hold everywhere: colour follows the entity, never its rank (filtering
must not repaint the survivors). No dual-axis charts. Sequential = one hue
light→dark. Status colours never become "series 4".

---

## 6. Interaction model

This is the real payoff of leaving Streamlit, and it should be treated as a
first-class requirement rather than polish.

- **`⌘K` command palette** — jump to any surface, project, recent conversation or
  entity; run any action (forget, re-embed, run ingest, resolve conflict). For a
  daily single-user tool this replaces most of the navigation.
- **`/`** focuses search from anywhere.
- **`j` / `k`** move through result lists, **`Enter`** opens, **`Esc`** goes back.
- **`g` chords** — `g o` overview, `g f` find, `g c` curate, `g p` operate, `g s` console.
- **`⌘\`** toggles the sidebar; **`⌘.`** toggles the facet rail.
- **The URL is the state.** Every facet, sort, view mode, page and scroll anchor
  is in the query string. Back/forward work. A view is a link you can paste into
  a note.
- **Optimistic mutation + undo** — destructive actions (forget, merge, resolve)
  apply immediately with a 5s undo toast, not a confirmation dialog. Confirmation
  dialogs are for multi-user systems; undo is right for a solo local tool.
- **Nothing ever full-page reloads.** Streamlit reruns the entire script on every
  widget interaction. Removing that is the largest daily-use improvement in this
  whole plan.
- Motion: 150–200ms, `ease-out` entering / `ease-in` exiting, transform+opacity
  only, `prefers-reduced-motion` respected. Nothing decorative.

---

## 7. Chart system

Form is chosen by the data's job, before any colour decision.

| Data | Form | Rules |
|---|---|---|
| Activity over time (14/30/90d) | Line + soft area, one series | No legend (title names it). Crosshair + tooltip. Gaps filled with zero, not interpolated. |
| Memory by category | Horizontal bar, sorted by count | Direct value labels. 6 hue slots + Other. 2px gap between bars. |
| Embedding coverage | **Not a chart** — stat tile + thin meter | A single percentage is a number, not a plot. |
| Drift audit over time | Small multiples (sampled / drifted / rate) | Never a dual axis. |
| Entity graph | Force-directed subgraph | Only ever the current result set. Full-graph render is banned — it is unreadable and slow. |
| Token/cost trend | Line, indexed to a common base if compared | Never two y-scales. |

Every chart carries a **table view toggle** — that is the accessibility fallback
and it is genuinely useful in a data tool. Legend present whenever there are ≥ 2
series. Recessive gridlines. Thin marks. No number on every point.

---

## 8. Phases

Acceptance criteria are written **before** the work, and are binary. A phase that
misses its bar is not re-scoped after the fact.

### Phase 0 — Query layer extraction *(no UI)* — ✅ **DONE 2026-08-09**

Move all SQL from `gui/page_views/*` into `throughline/queries/`. Measure the
three recorded query defects and fix the ones that reproduce.

**Bar:** every existing Streamlit page renders unchanged while importing only
from `throughline.queries`. Full test suite green.

*Original bar included "`search_semantic` shows an index scan in `EXPLAIN`".
That bar was dropped, not quietly relaxed: the query already produced an index
scan before any change, so the criterion could not distinguish a fix from a
no-op. It is replaced by a behavioural bar — a project-filtered search returns
the number of rows it was asked for — which the attempted optimisation failed.*

**Verdict: PASS.**

| Bar | Result |
|---|---|
| All SQL out of the pages | 14 of 15 pages use `throughline.queries`; `sql.py` (the console) still runs arbitrary SQL by design |
| Pages render unchanged | 15/15 render against a live DB — `tests/integration/test_gui_pages_render.py` |
| Full suite green | **415 passed** (from 385 at baseline; +30 new) |
| App still boots | `streamlit run gui/app.py` → HTTP 200, clean log |

Shipped: `throughline/queries/` with `_exec` (row/one/scalar/execute/batch +
identifier whitelisting) and eight query modules — `search`, `semantic`,
`memory`, `conversations`, `entities`, `skills` (skills/projects/prompts),
`activity` (time series + the 8 calendar event sources), `health`.

Of the three recorded defects: **one refuted** (HNSW — see above), **two
confirmed and fixed** (conflicts marker pushdown, 95–122× on the semantic
strategy; writer batching, ~5×).

Incidental fixes made while extracting, each of which was a real defect:

- The calendar formatted a timestamp into the SQL text of eight queries
  (`AND created_at >= '{cutoff}'::timestamptz`). Now one bound parameter.
- The knowledge graph built `IN (…)` lists by string-formatting a Python
  tuple, with a special case for the one-element `(1,)`. Now `= ANY(%s)`.
- `semantic_helper.similar_to_source` fetched the probe vector into Python,
  guessed its representation (`str` vs `list` vs unknown) and could pass
  `None` as the literal. Now a scalar subquery — the vector never leaves the DB.
- Project and prompt sort keys were interpolated into `ORDER BY`; now
  whitelisted, and an unknown key raises instead of silently mis-sorting.

**Not done, deliberately:** the stale-drift self-join in `conflicts.py` is
still O(n²) (1.9 s at 28k chunks — 9× cheaper than the semantic strategy was,
and 688 chunks is today's real corpus). Fixing it means changing detection
semantics (one `b` per stale chunk instead of every newer `b`), which is a
product decision, not an extraction. Tracked, not silently absorbed.

### Phase 1 — API + app shell + Overview — ✅ **DONE 2026-08-10**

FastAPI app, pooling, OpenAPI-generated client. Vite/React shell: routing, theme
tokens, sidebar, command palette skeleton. Overview surface.

**Bar:** `throughline serve` starts one process on one port and serves Overview
from live data. `⌘K` opens and navigates. Light and dark both render with no
hardcoded colour outside the token file. Palette validator passes on both surfaces.

**Verdict: PASS.**

| Bar | Result |
|---|---|
| One process, one port, live data | `throughline serve` → `/`, `/find`, `/curate`, `/operate`, `/console` all 200; unknown `/api/*` → 404 |
| `⌘K` opens and navigates | Verified in-browser; the `g f` chord also navigates and updates the URL |
| Both themes render | Verified in-browser, light and dark, through the real toggle |
| No hardcoded colour outside tokens | grep for hex literals in `shell.css` / `overview.css` / `index.css` → zero |
| Palette validator passes both surfaces | Categorical **and** both sequential ramps: ALL CHECKS PASS on `#FFFFFF` and `#0D1117` |
| Tests | **442 passed** (415 at end of Phase 0); `tsc -b` clean |

Shipped: `throughline/api/` (app factory, `ThreadedConnectionPool`, settings with
the loopback guard, `/api/overview`, `/api/health`), the `throughline serve`
command, and `web/` (Vite + React 19 + TS + Tailwind v4, token system, shell,
⌘K palette, `g`-chords, Overview).

**Corrections made during the phase**, each caught by measurement, not review:

- `psycopg_pool` was installed and then removed: it is the psycopg **3** pool,
  while the entire query layer is psycopg2. Two drivers for an async pool these
  endpoints never needed. Uses `psycopg2.pool.ThreadedConnectionPool` — no new
  dependency.
- **`--text-muted` failed WCAG AA** at 3.24–3.45:1 on light surfaces, and it
  styles section labels and hints — small text that needs 4.5:1. Re-derived by
  search: `#6b727a` / `#80878f`, now 4.57–5.21:1 across every surface pairing.
- **Both sequential ramps failed** light-end contrast at ~1.30:1, so the first
  bucket of any heatmap would have been invisible against the surface.
  Re-derived: 3.35:1 (light) and 2.06:1 (dark), monotone, single hue.
- **The theme toggle jumped across the sidebar** when the ⌘K hint auto-hid —
  `justify-content: space-between` losing a sibling. A control moving under the
  cursor is precisely the layout-shift rule this plan sets. Anchored right.
- **The build was not reproducible.** Tailwind v4's auto-source-detection was
  scanning `web/dist/*.js` — its own previous output — generating utilities from
  class-like strings inside it and inflating the CSS bundle by ~2.4 kB depending
  on whether a stale build happened to be present. Scoped with
  `@import "tailwindcss" source("../")`, then verified by planting a decoy stale
  bundle and confirming byte-identical output.
- The sparkline clipped its first and last points: at `x=0` and `x=w`, half of a
  2px stroke falls outside the viewBox. Inset by `padX`.

**Packaging decision implemented.** Vite builds into `throughline/web/` rather
than `web/dist`, so `package-data` ships the UI inside the wheel and
`pip install throughline` never needs Node. `web/node_modules/` and `web/dist/`
are ignored; `throughline/web/` is committed. FastAPI is an optional extra
(`pip install 'throughline[api]'`) and `throughline.api` imports lazily, so the
core package does not depend on it.

### Phase 2 — Find — ✅ **DONE 2026-08-10**

Hybrid retrieval endpoint, facets, List + Table modes, detail routes.

**Bar:** a query returns fused lexical+vector results in <300ms p95 on the live
DB. All facet/sort/page state round-trips through the URL. Back button restores
the exact prior view including scroll position. Table virtualizes at 50+ rows.

**Verdict: PASS.**

| Bar | Result |
|---|---|
| <300ms p95 on live DB | 20 queries: p50 **52ms**, p95 **155ms**, max 258ms |
| Facet/sort/page state in the URL | A pasted link restores query, kinds, categories, mode — verified in-browser |
| Back restores prior view + scroll | scrollY 900 → detail → back → **900** |
| Table virtualizes at 50+ rows | 200 results: 7200px spacer, **29 rows in the DOM** |
| Tests | **470 passed** (442 after Phase 1); `tsc -b` clean |

Shipped: `throughline/queries/find.py` (RRF fusion), `throughline/embedding.py`
(fail-soft backend resolution), `/api/find`, `/api/find/facets`,
`/api/detail/{kind}/{id}`, and the Find UI — query box, facet rail, List and
Table modes, six detail routes.

#### The lexical retriever was rebuilt after measurement

The first implementation scored with `similarity(content, term)`, mirroring
what the Streamlit page did. Measured against an ILIKE oracle on the real
corpus, it returned **8 of 474** true matches — 1.7% recall. A search that
looks like it works and silently isn't.

The cause is that `similarity()` compares *whole strings*, so a long document
containing a short term scores near zero. `word_similarity()` is the correct
metric but rechecks trigrams over full bodies, and this corpus has messages up
to **481,116 characters** (mean 673):

| strategy on 12k messages | time | recall vs ILIKE |
|---|---|---|
| `similarity(content, term)` | 887ms | 8 / 474 |
| `word_similarity(term, content)` | 793ms | 480 / 474 |
| **ILIKE filter + score on `left(content,600)`** | **56ms** | **480 / 474** |

So membership is decided by ILIKE — which is also what a user means by "find
X" — and rank by `word_similarity` over a bounded prefix, on the reasoning
that a term in the opening line is more likely the subject than one buried at
character 40,000. Fuzzy trigram matching is kept where it is cheap *and* most
useful: short fields (names, summaries, project names). Whole-query latency
went 850–920ms → 21–104ms as a side effect.

`tests/integration/test_find.py` asserts recall against the ILIKE oracle
directly rather than against a golden list, so this specific regression cannot
return quietly.

#### Other corrections during the phase

- **Scroll restoration was missing entirely.** Back returned to the top of the
  list. Added `<ScrollRestoration />`; it only works because React Query serves
  the previous result set from cache, so the page has full height on the first
  paint after navigating back.
- **Every message result showed its conversation's summary as the title**, so
  five distinct results rendered as five identical-looking rows. A message's
  own text is now the result; the conversation moved to the meta line.
- `psycopg_pool`-style mistake avoided in the UI layer too: highlighting builds
  React elements, not an HTML string. There is still zero
  `dangerouslySetInnerHTML` in the codebase — search results are precisely
  where user-controlled database content would otherwise reach markup.

~~**Known gap:** the URL-state helpers have no unit test.~~ **Closed
2026-08-10.** Vitest added, wired into the CI frontend job; 24 tests cover the
URL round-trip and CSV escaping.

Writing them found a real inconsistency the browser testing could not: page
sizes were coerced with `Number(x) || default`, so `per_page=0` fell back to
30 while `per_page=-10` clamped to 1 — two nonsense inputs, two different
outcomes. Anything invalid now lands on the same fallback.

### Phase 3 — Curate + Operate — ✅ **DONE 2026-08-10**

All seven curation queues with bulk actions and undo. Ingestion/scheduler/embed
control with SSE progress.

**Bar:** every mutation is undoable within 5s and reflected without a refetch of
the whole list. A long ingest streams progress without blocking the UI.

**Verdict: PASS.**

| Bar | Result |
|---|---|
| Mutation undoable within 5s | forget → toast → undo, verified in-browser: 2 → 1 → 2 rows |
| Reflected without refetching the list | Rows 3→2 and badge 3→2 with **exactly one** network call, `/api/curate/act` |
| Job streams without blocking | Console grew 1 → 20 lines while the UI stayed responsive |
| Tests | **496 passed** (470 after Phase 2); `tsc -b` clean |

Shipped: `throughline/queries/curate.py` (8 queues — the seven planned plus
Forgotten — and reversible mutations), `throughline/api/undo.py`,
`throughline/api/jobs.py` (subprocess runner + SSE), `/api/curate/*`,
`/api/operate/*`, and the Curate and Operate surfaces with undo toasts and a
live job console.

#### `forget` had to stop being a delete

The plan said mutations would go through `scripts/forget.py` semantics *and*
get an undo toast. Those two are incompatible: `forget_chunks` issues
`DELETE FROM memory_chunks`, so by the time the toast renders there is nothing
left to restore. An undo button over a hard delete is a lie.

Forgetting is now two-tier:

- **Forget** sets `status = 'forgotten'`. The row survives, drops out of every
  retrieval path, and is restorable — from the toast, or later from the
  Forgotten queue. `memory_chunks.status` is plain `text`, so this needed no
  migration.
- **Purge** (the original hard delete) remains, because soft-deleting a chunk
  containing a leaked credential does not remove the credential from the
  database. It is explicit, bulk, and labelled unrecoverable.

The half of this that is easy to get wrong is the second half: a "forget" that
only hides a row from one list, while the memory stays findable in search, is
worse than not offering the action. `find` and `semantic` now exclude hidden
statuses by default, and
`test_forget_hides_from_search_and_undo_restores` asserts both directions.

#### Corrections during the phase

- **The job console never appeared.** Its visibility test required the server
  status query to have already refetched and reported the job as running, so a
  job you had just launched showed nothing. Caught by driving the real UI, not
  by reading the code.
- `collect_status` does not report connection details at all — the Operate
  panel was rendering `database: None`. It now reads the same config helper the
  pool uses, so the panel cannot drift from what the server actually connected
  to. The password is stripped before serialisation.
- Undo tokens are single-use and expire; a replay returns **410** with a
  message pointing at the Forgotten queue rather than a bare error.
- Idempotency keys mean a double-clicked bulk action applies once instead of
  forgetting the same chunks twice and stacking two competing inverses.

**Scope note:** undo tokens live in process memory, not the database. That is
deliberate and bounded — they are a 5-second affordance for a mis-click, and
the underlying mutation is durably reversible regardless, so a server restart
costs a trip to the Forgotten queue rather than data. Any future *irreversible*
action must not be given an undo token; it needs a confirmation dialog instead.

### Phase 4 — Console, Timeline, Graph — ✅ **DONE 2026-08-10**

CodeMirror SQL console on a read-only role. FullCalendar timeline mode.
Cytoscape subgraph mode.

**Bar:** console cannot write (verified by attempting a write — it must be
rejected by Postgres, not by the app). Graph renders only result-set subgraphs.
Timeline reaches parity with the eight event sources in `calendar.py`.

**Verdict: PASS.**

| Bar | Result |
|---|---|
| Console cannot write, rejected by Postgres | 9 write forms, all `cannot execute … in a read-only transaction` — including a `DELETE` hidden in a CTE |
| Graph renders only result-set subgraphs | Induced subgraph only; an unrelated entity in the same DB never appears; empty sources → empty graph |
| Timeline parity with the 8 sources | All six find-able kinds browsable and plotted; entities via Graph, reflections via Curate |
| Tests | **525 passed** (496 after Phase 3); `tsc -b` clean |

#### Two library choices in the plan were not taken

The plan named CodeMirror, FullCalendar and Cytoscape. Two were dropped after
looking at what they were actually for here:

- **FullCalendar** is a scheduling widget — it wants events with start/end
  times in a month grid. This data is a stream of things that already
  happened, and the questions are "when was this busy?" and "what happened
  that day?". A density strip plus a day-grouped list answers both, themes
  from the same tokens, and avoids ~200 kB of calendar engine for a grid
  nobody schedules into.
- **Cytoscape** is a full canvas graph engine. The subgraph is bounded by
  construction (≤120 nodes), so ~400 kB buys nothing over `d3-force` (~40 kB)
  laying out SVG that honours the theme tokens.
- **CodeMirror** was also skipped for now: the console is a textarea with a
  schema sidebar and ⌘↵ to run. Syntax highlighting is a genuine gap, listed
  below rather than glossed over.

#### The write barrier is PostgreSQL's, not ours

Every statement runs inside `SET TRANSACTION READ ONLY`. The rejection comes
from the database:

```text
ERROR:  cannot execute DELETE in a read-only transaction
```

This replaced the plan's "read-only role", which would have needed superuser
to create and so would have silently degraded to an app-level guard on most
installs. The alternative — pattern-matching SQL text for dangerous keywords —
is security theatre: defeated by comments, casing and data-modifying CTEs,
while wrongly blocking a `SELECT` that merely contains the word "delete". Both
cases are asserted in `test_api_console.py`.

Honest limits, in the module docstring rather than buried: a read-only
transaction does not stop a volatile function with side effects elsewhere
(`dblink`, advisory locks), and does not stop an expensive query — that is
what the statement timeout and row cap are for. For a single-user local tool
whose operator already has `psql`, the console's job is preventing accidents.

#### Find gained a browse mode, because Timeline needed one

Search alone cannot answer "what happened in June?" — with no query text there
is nothing to rank, so the surface would be empty exactly when browsing. Find
now runs a time-ordered listing when filters are set without a query, which is
also what actually replaces the old Calendar page.

That surfaced a paging bug worth recording: the merge takes the top-N of each
kind, but SQL was breaking timestamp ties arbitrarily while the Python merge
broke them by `(kind, id)`. Page 2 therefore repeated rows from page 1 and
skipped others. Both sides now sort by the same total order, asserted by
`test_browse_pagination_is_stable_and_disjoint`.

#### Corrections during the phase

- **The graph never laid out.** `d3-force` drives ticks from
  `requestAnimationFrame`, which browsers suspend in a background tab — nodes
  stayed piled at the origin and never recovered. Layout is now computed
  synchronously (`sim.tick(320)`), which also makes it deterministic and stops
  it burning main-thread time animating a picture that settles immediately.
- The timeline repeated the Phase 2 message-title bug: a message's `title` is
  its *conversation's* summary, so every row in a conversation rendered
  identically. Fixed in both views.
- The "Text matching only" disclosure fired during *browsing*, where text
  matching is not what is happening. Search-degraded and listing-capped are now
  distinct messages.
- A single-day density strip rendered as one full-width bar, which reads as a
  rendering fault rather than as data. Hidden below two days.

**Known gap:** the SQL console has no syntax highlighting or completion —
schema browsing is a sidebar, not an autocomplete. CodeMirror 6 remains the
right answer if the console gets heavy use; it was not worth ~150 kB and a new
editor abstraction to reach this phase's bar.

### Phase 5 — Cutover — ✅ **DONE 2026-08-10**

`gui/` moves to `legacy/streamlit-gui/`. Docs, Docker, Makefile, README updated.
Zero `dangerouslySetInnerHTML`, zero emoji-as-icon, zero runtime font fetch.

**Bar:** parity checklist signed off page by page. `docker compose up` serves the
new UI. Lighthouse/axe pass on keyboard nav and focus visibility.

**Verdict: PASS.**

| Bar | Result |
|---|---|
| Parity checklist, page by page | All 14 pages mapped; 3 real gaps found and closed (below) |
| `docker compose up` serves the new UI | `throughline-web` healthy on `127.0.0.1:8787`, zero Streamlit markers |
| Keyboard nav + focus visibility | 7/7 tab stops show a visible ring, skip link first; all 5 surfaces clean on named controls, heading order, `<main>`, `<h1>`, no horizontal scroll |
| Tests | **505 passed** (525 before; the 20 GUI tests went with the GUI) |
| Build reproducible | CI staleness guard passes — committed `throughline/web/` matches source |

**Deleted:** `gui/` (19 files, 5,231 lines), its three test modules, the
`throughline gui` command, and Streamlit + pandas + plotly from the install.
`legacy/streamlit-gui/` in the original plan was dropped — the demo decision
made it dead weight, and the code is one `git checkout HEAD~1 -- gui/` away.

#### Parity was not automatic — three real gaps

"Every page is reachable" is not parity. Checking what the old pages could
*do* surfaced three losses, all closed rather than declared:

1. **The MCP server imported `semantic_helper` from `gui/`.** Deleting the GUI
   would have broken the path Claude Code actually reads memory through — the
   single most important consumer in the project. Repointed at
   `throughline.embedding` + `throughline.queries.semantic`, the same layer
   everything else uses. This is exactly why the checklist ran before the
   deletion rather than after.
2. **Export.** The old UI offered CSV/Excel/PDF on seven pages. Find now
   exports the current result set as CSV (UTF-8 BOM so Excel reads it), and
   the console exports query results.
3. **Creating a memory chunk by hand.** `examples/example_workflows.md`
   documented it as "Option C". Rather than let a documented workflow become
   false, Curate gained a New chunk form — undoable like every other mutation.

The Scheduler page was also nearly lost: it is a status view for an external,
optional, macOS-only launchd skill, unrelated to the pipeline job runner. It is
now a read-only panel in Operate (`throughline/scheduler.py`).

#### Corrections during the phase

- A bulk find-and-replace across the docs mangled four command examples into
  nonsense like `throughline serve run app.py`. Caught by grepping for the
  mangled shapes afterwards rather than trusting the edit.
- The container must bind `0.0.0.0` for published ports to work at all, which
  the loopback guard refuses by design. Resolved by setting
  `THROUGHLINE_ALLOW_REMOTE=1` *inside* the image while compose publishes on
  `127.0.0.1` only — the isolation is the container plus how the port is
  published, and both halves are commented where someone would change them.
- `doctor` was still checking for `streamlit`/`pandas`/`jinja2` as required
  packages, which would have reported a healthy install as broken.
- Two accessibility defects found by driving the real UI: the facet rail
  jumped from `h1` straight to `h3`, breaking heading navigation; and my first
  audit reported 22 missing focus rings, which was the *audit* being wrong —
  programmatic `.focus()` does not trigger `:focus-visible`, only real
  keyboard focus does.

**Total: ~28 working days.** That is the honest number for a solo rebuild of 15
surfaces plus an API layer — not 4 weeks of calendar time.

---

## 9. Open decisions

1. ~~**Demo mode.**~~ **Decided: drop the public demo.** At Phase 5 `gui/` is
   deleted along with `THROUGHLINE_DEMO_MODE` and its ~12 call sites, and
   Streamlit leaves the core install entirely. README links to
   kupermann.com/memory must go at the same time.
2. ~~**Node in a Python repo.**~~ **Decided and implemented (Phase 1):** Vite
   builds into `throughline/web/`, the assets are committed and shipped via
   `package-data`, and `pip install throughline` never needs Node. Still open:
   a CI check that fails when `throughline/web/` is stale relative to `web/src/`
   — without it a source-only commit silently ships the previous UI.
3. ~~**The `projects` table is empty.**~~ **Closed 2026-08-10 — and the
   diagnosis was wrong.** The table is not empty (53 registered rows), it
   *lags*: 81 project names are actually in use. The real defect was mine —
   the plan specified `/p/:name` for project detail and I built `/p/:id`, so
   every unregistered project was unreachable and invisible in the project
   listing, even though its memory was searchable.

   Fixed by treating the name as the identity and `projects` as optional
   enrichment: retrieval now starts from observed names with a LEFT JOIN to
   the registry, and detail is `/api/detail/project/by-name/{name}`. On the
   test database this took the project listing from 13 to 33. No backfill is
   required for the UI to be correct; `throughline backfill-projects` remains
   available for adding descriptions and status.

4. **pgvector is broken on the native `claude_memory` database** (found
   2026-08-09, pre-existing, unrelated to this work). `pg_extension` lists
   `vector 0.8.0`, but the library is gone:

   ```text
   ERROR:  could not access file "$libdir/vector": No such file or directory
   ```

   Homebrew's pgvector moved to 0.8.6 and dropped `postgresql@16`; the control
   file and `.so` now exist only for `@17`/`@18`, while the server is 16.14.
   Any query touching a `vector` column fails — not just similarity search. So
   semantic retrieval against the main memory DB is currently dead, and 11
   integration tests error on the default port.

   The Docker Postgres on 5433 has a working pgvector 0.8.2; all measurements
   and test runs in this document used it. Options: `pg_upgrade` to @17, build
   pgvector from source against @16, or move the memory DB into Docker.
   Needs a `brew`/`sudo` action, so it is the user's call.
5. ~~**Chart library.**~~ **Decided across Phases 1–4.** Everything is
   hand-rolled SVG plus `d3-force` (~40 kB) for graph layout. Recharts,
   Cytoscape and FullCalendar were all evaluated and none earned their weight:
   the charts are a single-series line and a bounded force graph, both of
   which need to honour the theme tokens and neither of which needs a
   rendering engine. Total frontend bundle: **492 kB / 155 kB gzipped.**

---

## 10. What this plan explicitly does not do

- No authentication, no multi-user, no roles. Single local user, loopback only.
- No mobile layout. Desktop-first, 1280px minimum. Responsive down to 1024px
  because it is cheap; below that it is out of scope.
- No real-time collaboration, no websockets beyond SSE job progress.
- No redesign of the ingestion adapters, the extraction pipeline, or the MCP
  server. Those are correct as they are; only their SQL moves.
