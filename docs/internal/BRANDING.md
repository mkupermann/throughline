# Branding Brief — Local Long-Term Memory for Claude Code

Prepared for public release on `github.com/mkupermann`, 2026-04-18.

---

## Context: The Naming Landscape is Brutal

Before naming: a reality check from a quick survey of GitHub (April 2026).

The `claude-*` memory space is *saturated*. Already shipped and active:
`claude-mem`, `claude-memory` (×5+ distinct repos), `claude-memory-compiler`,
`claude-echoes`, `claude-brain`, `claude-supermemory`, `claude-cognitive`,
`claude-engram` (×3), `claude-cortex`, `cortex-claude`, `claude-code-memory`,
`claude-code-rewind`, `cc-dejavu`, `claude-code-vector-memory`,
`claude-code-memory-setup`, `claude_memory`, `memory-mcp`, `mcp-memory-keeper`.

The classical-Greek space is exhausted: `mneme`, `anamnesis`, `lethe`,
`memento`, `mempalace`, `engram` (×6), `cortex` (×7+), `hippo-memory`.

A few more landmines:
- **Kairos** is an *internal Anthropic codename* exposed in the leaked Claude
  Code source (an unreleased "always-on" mode). Do not touch.
- **Cortex** is genuinely unusable; at least seven AI-memory repos own it.
- **Engram** is the second most-burned name after `memory` itself.
- Generic single words (`recall`, `remember`, `brain`) collide with dozens
  of existing packages on npm/PyPI and GitHub orgs.

**Naming strategy:** avoid `claude-*` prefixes entirely (crowded and brittle
if Anthropic rebrands), avoid classical-Greek cliches, lean toward
**concrete, memorable, slightly unexpected** English words that still evoke
persistence, memory, or knowledge accumulation. Two-syllable target.

---

## 1. Top-5 Name Proposals

### 1. **Throughline**

- **Etymology**: English compound (*through* + *line*). In narrative writing
  and design: the unbroken thread of meaning that runs through an entire
  story. Coined in its modern sense by Aristotle-via-Stanislavski, popularized
  in software by product and design culture.
- **Pro**: Exactly what the project does — preserves the continuous thread
  across otherwise disconnected Claude Code sessions. Developer-native
  vocabulary (used in product/design orgs daily). Zero existing AI-memory
  projects use it. Pronounceable, one word, memorable.
- **Con**: 10 letters is on the longer side. A few unrelated projects exist
  (a Rails tracing tool, a few small CLIs), but none in the AI/memory space.
- **Availability**:
  - `github.com/throughline` — org exists, inactive, no conflict for a repo
    at `github.com/mkupermann/throughline`
  - `github.com/mkupermann/throughline` — available
  - `pypi.org/project/throughline` — available (April 2026 check)
  - `throughline.dev` — likely available
- **Tagline**: *The thread that survives every session.*
- **Positioning**: Throughline is a local, PostgreSQL-backed long-term memory
  layer that turns scattered Claude Code sessions into one continuous,
  searchable engineering narrative.

---

### 2. **Marginalia**

- **Etymology**: Latin plural of *marginalis* — the notes written in the
  margins of a book. Since medieval manuscripts, marginalia has meant the
  accumulated *reader's* knowledge that a book does not contain on its own:
  corrections, cross-references, personal context.
- **Pro**: Extraordinarily apt metaphor. Claude Code sessions are the "book,"
  Throughline… sorry, *Marginalia* is the accumulated margin-notes that make
  the book useful to you specifically. Evocative, developer-literate,
  slightly literary without being precious. Not used by any AI memory project.
- **Con**: Four syllables. Some users will mis-spell it. A well-known
  independent search engine (`search.marginalia.nu`) exists but is an entirely
  different category — no trademark conflict for a developer-tool repo.
- **Availability**:
  - `github.com/marginalia` — user exists (unrelated, static)
  - `github.com/mkupermann/marginalia` — available
  - `pypi.org/project/marginalia` — available
  - `npmjs.com/package/marginalia` — occupied by a tiny abandoned package
    (last published 2019, unrelated domain)
- **Tagline**: *Every session leaves notes in the margin.*
- **Positioning**: Marginalia is a local knowledge graph that reads the
  margins of every Claude Code session so the next one starts with your
  accumulated notes already loaded.

---

### 3. **Holdfast**

- **Etymology**: Old English / Germanic compound (*hold* + *fast*). Two
  meanings both fit: (a) in biology, the root-like structure a kelp uses to
  anchor itself to bedrock — permanent attachment against drift; (b) in
  nautical / shipwright usage, a fixed clamp that *refuses* to let go.
- **Pro**: Strong, physical, one-word, two-syllable. Perfect verb/noun
  duality. Captures the "refuses to forget" USP cleanly. Not used anywhere in
  AI memory (a few unrelated game-mod repos only).
- **Con**: Slightly old-fashioned feel; could read as "anchor-y" rather than
  "smart." The biology metaphor requires one sentence of explanation.
- **Availability**:
  - `github.com/holdfast` — org exists but dormant, no repos
  - `github.com/mkupermann/holdfast` — available
  - `pypi.org/project/holdfast` — available
  - `holdfast.dev` — likely available
- **Tagline**: *Memory that refuses to drift.*
- **Positioning**: Holdfast is a local PostgreSQL + pgvector memory layer
  for Claude Code that anchors every session's decisions, patterns, and
  context so nothing drifts away between runs.

---

### 4. **Palimpsest**

- **Etymology**: Greek *palimpsestos* — a manuscript page scraped and
  reused, where the older writing still faintly shows through beneath the
  new. The defining metaphor for layered, accumulated memory: nothing is
  truly erased; each session writes over the last while leaving the
  earlier text recoverable.
- **Pro**: Uncommonly exact fit for a memory layer that consolidates,
  deduplicates, and surfaces historical context beneath current context.
  Zero AI-memory projects use it. Memorable, literary, feels substantial.
- **Con**: Hard to spell. Hard to pronounce on first sight (PAL-imp-sest).
  Slightly academic — not every developer will recognize the word.
- **Availability**:
  - `github.com/palimpsest` — user exists, dormant
  - `github.com/mkupermann/palimpsest` — available
  - `pypi.org/project/palimpsest` — available
- **Tagline**: *Every session, written over the last. None erased.*
- **Positioning**: Palimpsest is a local self-consolidating memory database
  for Claude Code — new knowledge layers over old without ever losing the
  earlier record.

---

### 5. **Keepsake**

- **Etymology**: 18th-century English compound (*keep* + *sake*). An object
  retained specifically *for the sake of* what it represents. Implies
  deliberate curation rather than exhaustive hoarding — which is literally
  what this project does via its extraction pipeline (it doesn't keep every
  token, it keeps the decisions, patterns, and insights).
- **Pro**: Warm, short, extremely memorable, two syllables. Captures the
  *selective* nature of the extraction (insights, decisions, error_solutions
  — not raw transcripts). Developer-friendly but not cold.
- **Con**: There *is* an existing project at `github.com/replicate/keepsake`
  (a dormant ML-versioning tool from Replicate, last commit ~2021). Different
  category, but it does consume the bare name. Slightly sentimental tone —
  some engineers will find that a negative.
- **Availability**:
  - `github.com/keepsake` — user exists, dormant
  - `github.com/mkupermann/keepsake` — available
  - `pypi.org/project/keepsake` — taken by Replicate's inactive package
    (would require a disambiguating package name like `keepsake-memory`
    or `keepsake-db`)
- **Tagline**: *Not everything, just what matters.*
- **Positioning**: Keepsake is a local long-term memory for Claude Code that
  extracts only what's worth keeping — decisions, patterns, error fixes —
  and hands it back the next time you need it.

---

## 2. Top Recommendation: **Throughline**

Throughline is the strongest choice, and it's not close.

**Why it wins over the others:**

1. **It names the actual problem, not the mechanism.** Every competing
   project names the *how* (memory, engram, cortex, brain, mem0). Throughline
   names the *what you get*: narrative continuity across sessions. That's the
   pitch. That's why someone installs it.
2. **It's developer-native.** "Throughline" is already in the working
   vocabulary of every PM, designer, and staff engineer. It doesn't need a
   dictionary aside like *palimpsest* or a nature metaphor like *holdfast*.
3. **Clean namespace.** No meaningful GitHub / PyPI / npm collision in the
   AI-memory category. No classical-Greek cliche. No `claude-*` prefix that
   would look stale if Anthropic rebrands the CLI.
4. **It scales.** If the project grows beyond Claude Code (e.g. Cursor,
   Codex, Gemini CLI), the name still works — a throughline is a throughline
   regardless of which tool writes it. A name like `claude-echoes` or
   `claude-cortex` would become a liability.
5. **Pronounceable, one word, memorable, lowercase-friendly** (`throughline`,
   `tl`, `tl/memory`).

**Runner-up:** *Marginalia* if you want a more literary, slightly quirkier
brand. It's more distinctive but demands more explanation. Throughline is
the safer *and* more strategic pick.

**Avoid from this list:** *Keepsake* (burned by Replicate's namespace) and
*Palimpsest* (too hard to spell on a terminal under deadline).

---

## 3. Tagline Options

For the README header — pick one, keep the rest for social / docs.

1. **The thread that survives every session.**
2. **Local long-term memory for Claude Code.** *(the descriptive fallback)*
3. **Every session, continuous. Every decision, remembered.**
4. **Your Claude Code sessions, woven into one engineering memory.**
5. **Persistent context for Claude Code — on your machine, in Postgres,
   under your control.**

**Recommended pairing for the README:**
- H1: **Throughline**
- Subtitle: *The thread that survives every session.*
- One-liner below: *Local long-term memory for Claude Code — PostgreSQL +
  pgvector + knowledge graph, 100% on your machine.*

---

## 4. GitHub Repo Description (≤350 chars)

> **Throughline — Local long-term memory for Claude Code. Automatically
> ingests every session into PostgreSQL, extracts decisions, patterns, and
> insights, builds a knowledge graph with pgvector semantic search, and
> pre-loads relevant context into every new session. 100% local, no cloud,
> no API keys required with a Claude Max plan.**

(347 characters.)

---

## 5. GitHub Topics (14)

```
claude-code
claude
ai-memory
long-term-memory
persistent-memory
postgresql
pgvector
knowledge-graph
semantic-search
local-first
rag
ollama
streamlit
developer-tools
```

Optional additions if you want broader reach: `anthropic`, `llm`,
`context-engineering`, `privacy-first`.

---

## 6. Marketing Bullet Points (for README feature list)

1. **Continuous memory across every Claude Code session.** Session JSONL
   files are ingested automatically into PostgreSQL — no manual exports,
   no copy-paste.
2. **Structured extraction, not blind retention.** Claude itself extracts
   decisions, patterns, insights, preferences, contacts, error solutions,
   project context, and workflows from every session.
3. **Knowledge graph out of the box.** Entities, relationships, and a
   timeline are maintained automatically — query who, what, when, and why
   without leaving your terminal.
4. **Semantic search via pgvector** — powered by local Ollama embeddings
   by default, OpenAI-compatible if you prefer.
5. **Auto-injected context on every session start.** The context pre-loader
   surfaces relevant prior decisions and patterns the moment a new Claude
   Code session opens.
6. **Self-reflecting.** Deduplicates, detects contradictions, and
   consolidates stale memories so the database stays signal-dense.
7. **14-page Streamlit GUI** — dashboards, calendar, conversations, memory
   health, knowledge graph, prompts, scheduler, ingestion, semantic search,
   raw SQL. Point, click, inspect.
8. **100% local, zero cloud dependency.** PostgreSQL on your machine.
   Your sessions never leave it. Free to run if you already have a Claude
   Max plan — extraction uses the `claude` CLI, not the metered API.

---

## 7. ASCII Logo (optional, README-header)

Two options. Pick whichever fits the README tone; both are narrow enough to
survive GitHub's markdown rendering.

**Option A — minimal, the literal "through-line":**

```
  ·──·──·──·──·──·──·──·──·──·──·──·──>
                  throughline
      local long-term memory for claude code
```

**Option B — sessions-as-dots, one thread binding them:**

```
   session   session   session   session   session
      ●─────────●─────────●─────────●─────────●──▶
                     throughline
```

Recommended: **Option B**. It tells the story in one glance — discrete
sessions, continuous thread.

---

## Appendix: Names Considered and Rejected

| Name | Reason rejected |
|---|---|
| `claude-memory` / `claude-mem` / `cc-memory` | Already taken, crowded |
| `cortex` / `engram` / `mneme` / `anamnesis` | Burned — 3–7 identical projects each |
| `kairos` | Anthropic internal codename (leaked source) |
| `recall` / `remember` / `brain` | Generic, many collisions on PyPI/npm |
| `lethe` / `memento` / `mempalace` | Greek / Latin memory cliche, taken |
| `hippocampus` / `hippo` | Taken (`hippo-memory`) + hard to type |
| `dejavu` / `rewind` | Taken by `cc-dejavu` and `claude-code-rewind` |
| `chronicle` / `ledger` / `tome` | Generic; `chronicle` has big Google project |
| `synapse` / `neuron` | Enterprise-software feel, many collisions |
| `echoes` | Taken (`claude-echoes`) |

---

*End of brief. Ship it.*
