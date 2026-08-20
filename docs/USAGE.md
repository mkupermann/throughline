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

The writer recognizes those known prompts and drops the source file at ingest;
it records a zero-row decision in `ingestion_log` and creates no conversation.
`conversations.generated_by` is for rows already stored before this guard
existed. Listings, charts, search, and answers exclude those legacy labelled
rows by default.

If you upgraded from a version before this existed, label what is already
stored:

```bash
python -m throughline.jobs.backfill_generated_by --dry-run   # count first
python -m throughline.jobs.backfill_generated_by
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

Retrieval is entirely local. Content can leave the machine when a hosted model
is selected for embedding, answering, extraction, titles, or reflection:

| Variable | Purpose |
|---|---|
| `THROUGHLINE_ANSWER_BACKEND` | `auto` (default), `ollama`, `openai`, `claude` |
| `THROUGHLINE_ANSWER_MODEL` | model name for that backend |
| `THROUGHLINE_ANSWER_BASE_URL` | any OpenAI-compatible server — LM Studio, llama.cpp, vLLM, LiteLLM |
| `OPENAI_API_KEY` | makes embedding `auto` use hosted OpenAI; also enables the OpenAI model backend |

These variables control answering. Extraction, titling, and reflection are also
model operations and can send their respective content to a hosted provider;
use their documented local configuration where content must remain on the
machine. `THROUGHLINE_MEMORY_LANG` forces the language extracted memory and
generated titles are written in; left unset they follow the session, so German
sessions produce German memory and English ones English.

For embeddings, `auto` chooses OpenAI when `OPENAI_API_KEY` is set, otherwise
Ollama. Answering can select a configured remote OpenAI-compatible endpoint,
or hosted OpenAI; other model operations can also use hosted
providers. `throughline doctor` prints which model will answer and whether it
runs locally; the UI says so on screen with every answer. `--model` overrides
the model for a single question.

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

## Taking it out again

The corpus is worth more than the tool that holds it. `export-markdown` writes
it back out as plain Markdown — one folder per project, sessions oldest first —
so it stays readable in Obsidian, in a text editor, or in ten years.

```bash
throughline export-markdown --out ~/Obsidian/Throughline
throughline export-markdown --out DIR --project throughline   # one project
throughline export-markdown --out DIR --since 2026-01-01      # recent work only
throughline export-markdown --out DIR --tool-output 400       # keep tool output
throughline export-markdown --out DIR --redact                # scrub keys, tokens, emails, home paths
```

The same export runs from the Operate page in the web UI, where the destination
is a field rather than a one-click Run, and the run streams its output like any
other job.

Each session becomes a dated section. Inside it every turn is labelled by kind —
`Prompt`, `Answer`, `Execution` — and every assistant turn names the model that
produced it, because a long session routes work to whatever model fits and the
session header cannot say which one wrote a given paragraph. Commands appear
verbatim, and every file the assistant created or changed is a `file://` link.

Tool *output* is omitted by default: it is the bulk of the corpus and the least
readable part of it. So are the tool's own model calls; `--include-generated`
puts them back. `THROUGHLINE_AUTHOR` sets the label on your own prompts
(default: `You`).

A project that would land above roughly 1.5 MB splits into dated parts, because
an editor asked to open a ten-megabyte Markdown file stops being an editor. The
split never reorders or drops a session, and a single very long session stays in
one piece.

Re-running updates the same folder rather than rebuilding it. The export keeps
a manifest of what it wrote (`.throughline-export.json`) and uses it three ways:
a file whose content has not changed is left alone, so the modification date
still means something and a synced folder does not re-upload the lot; a file the
last run wrote and this one no longer produces — a part that disappeared because
a project now splits differently — is deleted; and anything not in the manifest
is never touched, so your own notes can live in the same vault safely.

Only `README.md` changes on a run where nothing else did: it records when the
export last ran.

A service-triggered export is confined to `THROUGHLINE_EXPORT_ROOT` (your home
directory by default) and its destination is validated before anything runs —
see [SECURITY.md](../SECURITY.md). The command line is not confined: it runs as
you do.

The transcripts hold whatever your sessions held, so an export into a
cloud-synced folder puts that content wherever the folder goes. `--redact` runs
every exported text through the same heuristic pass that guards memory
extraction ([`throughline/pii.py`](../throughline/pii.py)): API-key shapes, JWTs,
bearer headers, `password=` assignments, private-key blocks, email addresses,
and home-directory usernames. It is conservative by design and will miss an
unusual secret shape, so treat it as reducing exposure rather than removing it.
A redacted path can no longer resolve, so file references render as plain code
instead of links — a link that silently fails is worse than an honest path.

## Moving the corpus to another database

One-way, for the step where a native install becomes the Compose stack a
second machine will replicate from:

```bash
throughline consolidate --target-url postgresql://user@127.0.0.1:5433/throughline --dry-run
throughline consolidate --target-url postgresql://user@127.0.0.1:5433/throughline
```

The dry run prints both row counts side by side and moves nothing. The real
run empties the target, loads the source, and compares every table
afterwards — the source is never modified and remains the fallback until the
counts agree.

Emptying the target is not optional. `pg_restore --clean` alone fails to drop
a table other tables reference, reports that as an ignored error, and the load
that follows *appends*: a target holding 762 conversations ended up with 4,645
after loading a source of 3,883, while every table nothing referenced was
replaced correctly. That is the kind of partial success that reads as success,
which is why the row comparison is part of the command rather than advice.

A password in the URL is moved into `PGPASSWORD` before anything runs, because
`argv` is visible to every process on the machine.

### Carrying it to a machine that cannot see this one

```bash
# here
throughline consolidate --export-to ~/transfer/corpus.dump

# there, after copying both files across
throughline consolidate --from-dump corpus.dump \
    --target-url postgresql://throughline@127.0.0.1:5433/throughline
```

The export writes the archive and a `corpus.dump.counts.json` beside it. Carry
both: the far machine cannot reach the source to compare against it — that is
the whole reason the archive exists — so the counts travel with it and the
restore checks one against the other. A restore without them is refused rather
than performed blind, because a half-restored corpus looks exactly like a whole
one until you go looking.

## A standing link between two machines

Bidirectional replication needs each node to reach the other's PostgreSQL.
Opening a database port on the LAN is not the way: this corpus is served by a
`trust`-configured PostgreSQL, so whoever reaches the port is in.

One SSH connection carries both directions, so only the machine being dialled
needs an SSH server:

```bash
throughline tunnel --host framework.fritz.box --user michael \
    --identity ~/.ssh/id_ed25519_throughline
```

`-L` makes the peer's database reachable here on `127.0.0.1:5434`; `-R` makes
this one reachable there on the same port, over the same socket. Both forwards
bind to loopback, so neither database is ever on the network.

For it to stand rather than be started by hand, use
[`launchd/com.throughline-tunnel.plist`](../launchd/com.throughline-tunnel.plist).
`KeepAlive` restarts ssh whenever it exits, which is what "connected whenever
both machines are on the network" means in practice: while the other machine is
asleep or elsewhere, ssh fails and launchd retries. Expect the log to fill with
connection failures during those stretches — that is the retry loop working,
not a fault.

`ExitOnForwardFailure=yes` matters more than it looks: without it ssh stays up
when a forward could not bind, so the supervisor sees a healthy process while
replication sees nothing.

## Checking on it

```bash
throughline status                       # counts, coverage, what is pending
throughline doctor                       # environment, schema, adapters, backups
throughline doctor --category archive    # store consistency and backup age
throughline migrate --status             # pending schema migrations
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
