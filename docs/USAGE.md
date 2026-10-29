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

Retrieval is entirely local. Content can leave the machine when a named remote
model is selected for embedding, answering, extraction, titles, or reflection:

| Variable | Purpose |
|---|---|
| `THROUGHLINE_ANSWER_BACKEND` | `auto` (default), `ollama`, `compatible` (`openai` is a deprecated alias) |
| `THROUGHLINE_ANSWER_MODEL` | model name for that backend |
| `THROUGHLINE_ANSWER_BASE_URL` | an OpenAI-compatible server you name — LM Studio, llama.cpp, vLLM, LiteLLM |
| `THROUGHLINE_API_KEY` | bearer token for that endpoint (never hosted OpenAI) |

These variables control answering. Extraction, titling, and reflection are also
model operations and can send their respective content to a named remote
endpoint; use their documented local configuration where content must remain on
the machine. `THROUGHLINE_MEMORY_LANG` forces the language extracted memory and
generated titles are written in; left unset they follow the session, so German
sessions produce German memory and English ones English.

For embeddings, `auto` uses a local Ollama embedding model only. Hosted OpenAI
is not an embeddings backend. For generation, the probe is Ollama, then an
OpenAI-compatible endpoint you named in `THROUGHLINE_ANSWER_BASE_URL`.
`throughline doctor` prints which model will answer and whether it
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

## Notes and the time they were valid

A note has two clocks. `created_at` says when Throughline recorded it. `valid_from` and `valid_to` say when the statement was true. Where a note starts depends on how it came in. A note pinned in the UI or written through MCP is valid from now. An extracted note starts at the `source_at` of its conversation window. An agent-memory note starts at the modification time of its file, never later than now. A replacement starts where you say, and otherwise as described below. Notes that existed before migration 012 start at their source date, or at their creation date. `valid_to` stays empty until something ends it.

Replacing a note (`POST /api/memory/{id}/supersede`, or the MCP `supersede` tool) takes two optional fields:

- `kind`: `update` (the default) means the old note was right until now. It stays valid until the new one starts, and the new one starts now unless you give a date. `correction` means the old note was wrong from the beginning. Its interval is emptied, and the new note starts where the old one started.
- `valid_from`: the start of the new note, as RFC 3339 or `YYYY-MM-DD`.

A start in the future is refused with 422 `valid_from_in_future`. A start before the note being replaced is refused with 422 `valid_from_before_predecessor`. A star on the old note moves to the new one.

To ask what held on a given day, use `GET /api/memory?valid_at=2026-01-15`. To ask what Throughline knew on that day, use `known_at`. Both take RFC 3339 or `YYYY-MM-DD`, and either one replaces the `status` filter. A plain date means the end of that day in UTC (at most now), so a question about today includes what changed today. A note counts as valid until the earlier of `valid_to` and `expires_at`. Forgotten notes never appear in these answers. A date that does not parse gives 422 `validation_error` and names the field.

Only replacing and merging end a validity. Forgotten and stale notes keep an open `valid_to`, because nobody said the statement stopped being true. `POST /api/memory/{id}/keep` brings such a note back and clears `expires_at`. If the note has a successor, keep answers 409 `has_successor`, since reviving it would put two answers side by side.

The agent-memory import follows the same rules. A changed section becomes a successor through update, starting at the file's modification time (never later than now). A section that vanished from the file is marked superseded with an end date. Nothing is deleted. If you replaced an imported note in the UI or over MCP, the import keeps your version as long as the file section stays as it was. Once the section changes, its new text replaces your version, because the newer change wins.

Replacing a forgotten note keeps it forgotten. It points at its successor and gets an end date, but it shows up neither in History nor in an answer about a date. The import leaves a forgotten note forgotten when its section vanishes.

## Leaving things out of the ingest

Some paths and projects should never reach the corpus at all. The ingest
exclusion list skips them *before* they are parsed: what is excluded is never
written to the database, and the log keeps only a counter and the rule kind —
never the path or the content of an excluded file.

Rules come from three optional sources and are combined (any match excludes):

- `~/.throughline-ignore` — one rule per line, `#` starts a comment
- `THROUGHLINE_INGEST_IGNORE` — comma-separated rules
- `throughline ingest --ignore <rule>` — repeatable

A rule is `path:<glob>` (matches the absolute session file path, `*` and `**`
supported), `project:<name>` (exact match on the recognised project name,
case-insensitive), or a bare glob, which is a `path:` rule.

Three examples:

```bash
# 1. Path glob: never ingest one noisy session directory
throughline ingest --ignore 'path:**/scratch/**'

# 2. Project name: never ingest a whole project
throughline ingest --ignore 'project:client-acme'

# 3. Combination: both, plus a pattern from the environment
export THROUGHLINE_INGEST_IGNORE='path:**/*.bak.jsonl'
throughline ingest --ignore 'path:**/drafts/**' --ignore 'project:client-acme'
```

The same rules can live in `~/.throughline-ignore`:

```text
# ~/.throughline-ignore
path:**/scratch/**
project:client-acme
```

Be honest about the scope: **the list acts at import time.** Whatever was
imported earlier stays in the corpus until you delete it (Agent Memory →
Forget, or drop the rows). An excluded file is never read or hashed, an excluded project
conversation is dropped before any write — but nothing here rewrites history
that is already in the database.

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

## The continue loop

The handoff brief is only useful if it reaches the next session without
ceremony. Three routes, pick what fits your tools.

**Clipboard, one keystroke.** `--copy` puts the brief on the system clipboard
(Windows `clip`, macOS `pbcopy`, Linux `wl-copy` or `xclip`). A shell alias
makes it a reflex:

```bash
alias tlh='throughline handoff --copy'
tlh --project myproject
```

**Claude Code, automatic.** A SessionStart hook in `~/.claude/settings.json`
runs the command when a session starts, and its output lands in the session
context. No pasting.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          { "type": "command", "command": "throughline handoff --project myproject --limit 10" }
        ]
      }
    ]
  }
}
```

The `matcher` takes `startup`, `resume`, `clear` or `compact`. Database
variables must be visible to that command, so set them in your user
environment, not only in a compose file.

**Cursor and other rule-file tools.** `--out` writes the brief as a file a
rule can point at. It refuses to overwrite, so refresh means delete first.
That is deliberate: no tool silently replaces a file you may have edited.

```bash
rm -f .cursor/throughline-handoff.md
throughline handoff --project myproject --out .cursor/throughline-handoff.md
```

Then reference the file from a rule in `.cursor/rules` so the agent reads it
at session start.

Without flags `handoff` keeps printing to stdout, unchanged. The brief is
bounded either way. The context belongs in the session, the evidence stays in
the store.

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

## Retention and archiving

Deleting is a decision, not a background job. `retention` is an explicit
command — you or your cron call it — and **dry-run is the default**: it prints
what a policy would hit and changes nothing.

```bash
# Dry-run: what would older-than 200 days touch?
throughline retention --older-than 200

# Apply: archive first (export-markdown --evidence shape), then delete
throughline retention --older-than 200 --archive-dir ~/Throughline/archive/2026-10 --apply

# Deliberate short window: refused without the override
throughline retention --older-than 30 --archive-dir DIR --apply
#   → error: shorter than 180 days … EU AI Act Art. 19 …
throughline retention --older-than 30 --archive-dir DIR --apply --allow-short-retention
```

The measure is the conversation's `ended_at` (falling back to `started_at`).
`--project` narrows to one project. Linked `memory_chunks` (`source_id` points
at the conversation) go with it; active notes that stand on their own are never
touched. Embeddings of deleted messages are cleaned up with them.

`--apply` without `--archive-dir` aborts before anything runs. The archive is
written first — one Markdown file per session plus `evidence-manifest.json`, the
same shape as `export-markdown --evidence`, and `memory-chunks.json` with the
full rows of the linked notes — and only then does a single transaction delete
the rows. Windows under 180 days need
`--allow-short-retention`; the report records when that override was used. Each
applied run writes one summary line into `memory_reflections` (policy, counters,
archive path) so the deletion stays reviewable.

**Backups are untouched.** Retention only deletes rows in the live database.
`throughline backup` dumps and the backups they leave stay exactly as they are —
a deleted conversation is still in last week's dump until that dump is pruned.

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
throughline tunnel --host otherhost.local --user alex \
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
