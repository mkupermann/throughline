# Usage Guide

Installation is covered in [INSTALLATION.md](INSTALLATION.md) and
[DEPLOYMENT.md](DEPLOYMENT.md). This is what to do with it afterwards.

## The daily loop

Ingestion runs on a schedule, so most days you do nothing at all. The tool
reads the session files your assistants already write, normalises them, and
adds what is new. Nothing you do differently in Claude Code or Vibe or Codex is
required for it to work — that is the point.

You come to Throughline when you need something back:

```bash
throughline ask "why did we drop the queue-based ingest?"
throughline search "pgvector index"
throughline serve                # then read it in the browser
```

If you have not set up the schedule, ingest by hand — it is idempotent, so
running it twice costs nothing but a second:

```bash
throughline ingest --list-sources   # which tools are installed, and what is waiting
throughline ingest --all
```

## The web UI

`throughline serve` puts the UI and its JSON API on one port
(`http://127.0.0.1:8790`). Six surfaces are in the navigation; two more you
reach by following something.

**Overview** answers "what should I deal with?" — the pipeline's own state
first (material waiting to be ingested, chunks without embeddings, contradictions
found), then the projects you actually worked in over the last seven days.

**A project's page** opens from there. It lists that project's sessions rather
than its messages: one project on a real corpus holds 7,284 messages, and a
page that renders all of them is a page that never finishes. Sort newest-first
to answer "what was I just doing", oldest-first to read the project as a story.
The search box searches inside the project — session titles and the full text
of their messages, server-side.

**A session** opens from there in turn, and shows the whole transcript: what
you asked, what the assistant answered, the commands it ran, and what those
commands returned. A transcript that shows only prose is missing most of the
work — on one 5,560-message session, 772 assistant messages have no prose at
all, because they are entirely tool calls.

**Find** is one query across everything: conversations, messages, memory chunks,
skills, projects and prompts. Lexical and semantic retrieval run together and
their rankings are fused, so an exact identifier and a vague description both
work. Facets narrow by type, project or tool; `↓` from the search box moves into
the results, then `j`/`k` walks them with a reading pane open.

**Ask** sits beside the search box on Find. It answers in prose from your own
records and cites each claim — click a citation and you land on the message or
chunk it came from. An answer with no citations is labelled unverified, and a
question the records cannot answer is told so instead of guessed at.

**Timeline** is the same corpus by time instead of by query: one column per day
across every tool, opening on the most recent day with activity, drilling into
a session and its transcript.

**Curate** is the queue of things that make memory less trustworthy —
contradictions, drift, superseded chains, low confidence, missing embeddings,
expiring, never accessed, forgotten. Bulk actions with a confirmation before
anything is forgotten, and an undo after.

**Operate** shows the pipeline and runs the jobs that change it, streaming
their output live. **Console** is a read-only SQL prompt: every statement runs
in a `READ ONLY` transaction, so writes are refused by PostgreSQL itself rather
than by a keyword filter.

`⌘K` opens the command palette, `/` focuses search, `g` then `o f t c p s`
jumps between surfaces.

### What the counts leave out

Throughline calls a model to title conversations, extract memory, and answer
questions. Those calls are themselves sessions on disk, so ingestion collects
them like any other: on the corpus this was written against, 3,017 of 3,606
stored conversations were the tool talking to itself.

They are labelled at ingest — `conversations.generated_by` names the script
that produced them — and every listing, chart, search and answer excludes them
by default. Nothing is deleted: a project page reports how many it is
withholding and shows them on request, and the column is there to query in
Console.

If you upgraded from a version before this existed, label what is already
stored:

```bash
python3 scripts/backfill_generated_by.py --dry-run   # count first
python3 scripts/backfill_generated_by.py
```

## Getting answers back

```bash
throughline ask "what did we decide about embeddings?"
throughline ask "why is ingestion slow" --project throughline
throughline ask "which tools do I actually use" --json
```

`ask` retrieves the nearest records, hands them to a model, and requires it to
cite. `--top-k` widens or narrows retrieval; recall@24 measured 75% against
recall@12's 60% on this corpus, which is why 24 is the default.

Retrieval is entirely local. The one moment stored content leaves your machine
is the prompt sent to a *remote* answering model — so point it at a local one
and it never does:

| Variable | Purpose |
|---|---|
| `THROUGHLINE_ANSWER_BACKEND` | `auto` (default), `ollama`, `openai`, `claude` |
| `THROUGHLINE_ANSWER_MODEL` | model name for that backend |
| `THROUGHLINE_ANSWER_BASE_URL` | any OpenAI-compatible server — LM Studio, llama.cpp, vLLM, LiteLLM |
| `OPENAI_API_KEY` | read only by the `openai` backend |

`auto` probes Ollama first, so a machine running a local model never makes a
network call and never had to be configured not to. `throughline doctor` prints
which model will answer and whether it runs locally; the UI says so on screen
with every answer. `--model` overrides the model for a single question.

`THROUGHLINE_REDACT_PROMPTS=1` strips secrets from the excerpts before they are
sent. It is off by default, deliberately: this is your own history on your own
machine, and a memory tool that hides your own credentials from you is failing
at its job. Turn it on for a shared database or a hosted model you do not
control.

## Keeping memory trustworthy

The reflection engine handles the routine cases — near-duplicates, chunks that
contradict newer ones, facts that have gone stale:

```bash
throughline reflect                     # all four modes
throughline reflect --mode dedup
throughline reflect --dry-run           # see what it would do
throughline conflicts                   # cross-tool disagreements
```

To remove something for good, use **Curate → Forget** in the UI or the
`memory.forget(ids, reason)` MCP tool. Forgetting cascades through the
embeddings, repairs dangling `superseded_by` references, and writes an audit
row to `memory_reflections` with your reason — so what was removed, and why,
stays answerable afterwards.

## Reading memory from inside your assistant

Two routes, and they compose:

**MCP.** `memory_mcp` exposes search, recall, write, supersede and forget over
the Model Context Protocol, so any MCP-capable client can query the shared
store mid-session. See [`memory_mcp/README.md`](../memory_mcp/README.md) for
the tool list and the project-scoping rules.

**SessionStart hook.** `throughline install-hooks` writes a hook into
`~/.claude/settings.json` that drops a short, project-scoped summary of past
decisions and preferences into each new session — context without asking for
it.

## Checking on it

```bash
throughline status                       # counts, coverage, what is pending
throughline doctor                       # environment, schema, adapters, backups
throughline doctor --category archive    # store consistency and backup age
python3 scripts/migrate.py --status      # pending schema migrations
```

`doctor` is the one to run when something behaves oddly: it checks Python,
PostgreSQL and pgvector, every adapter, the embedding and answering backends,
the schedule, and whether a recent backup exists.

## Performance

- First ingestion of a large `~/.claude/projects/` (100+ sessions) takes
  30–90 seconds. Later runs only parse files that changed.
- Memory extraction runs at roughly one conversation per 15–30 seconds.
- Embeddings with Ollama on Apple Silicon: about 20 chunks/second. OpenAI
  `text-embedding-3-small` at batch size 100 does about 200 chunks per call.

Measured numbers and tuning: [BENCHMARKS.md](BENCHMARKS.md) and
[PERFORMANCE.md](PERFORMANCE.md).

## Things worth knowing

- **The database outlives its sources.** Assistant CLIs rotate transcripts
  away; on the corpus measured here, 91% of the ingested Claude Code sessions
  no longer existed on disk. For those, this is not an index — it is the only
  copy. Schedule `scripts/install_backup_agent.sh` and let `doctor` tell you
  when the last dump is getting old.
- **A project is a working directory**, not a label you maintain. Sessions
  started from your home directory or `/tmp` are grouped as `(no project)`
  rather than being claimed as one.
- **Tag memory chunks liberally.** Filtering by tag is cheaper and more precise
  than full-text search over the same content.
