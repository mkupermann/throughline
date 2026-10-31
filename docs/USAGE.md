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
throughline reflect                     # all three modes (dedup, stale, contradictions)
throughline reflect --mode dedup       # merges exact-prefix duplicates, see below
throughline reflect --dry-run           # see what it would do
throughline conflicts                   # cross-tool disagreements
```

To take a note out of every answer, use **Curate → Forget** in the UI or the
`forget` MCP tool. Forget sets the note's `status` to `forgotten` and updates
`updated_at`. Nothing else happens: the row and its text stay in the database,
the embeddings stay, `superseded_by` links are not repaired, no audit row is
written and the optional `reason` of the MCP tool is not stored. Forgotten notes
appear in no answer. (The old Python version also cascaded through embeddings and
wrote an audit row; the Rust version does not.)

## How often a note is used, and how strong it is

Every note keeps `access_count` and `last_accessed`. Both change when a note is handed to an agent or to the command line: the MCP tools `recall`, `project_context`, `get_note` and `keep`, MCP `prompts/get`, MCP `resources/read` for a note or a context pack, the CLI commands `recall` and `context`, and `POST /api/memory/{id}/keep`. `GET /api/recall` and `GET /api/context` count only when the request carries a valid agent key (see [Agent access over HTTP](#agent-access-over-http)); without one they do not. Without an `Authorization` header, or with a scheme other than Bearer (such as `Basic` from an auth proxy in front of the UI), the request is the UI: answered and not counted. A Bearer header with a wrong or empty key, or while agent access is off, gets 401. These never count: `search` (MCP, HTTP or CLI), `conflicts`, `resources/list`, ask, browsing in the UI and any other HTTP route. Counting is per note. There is no count per agent, key or time of day.

From that Throughline derives a strength between 0.05 and 1:

```text
strength   = 0.05 + 0.95 * 2^(-days / half_life)
half_life  = 30 * (1 + ln(1 + uses)) days
```

Pinned notes and decisions get a half-life four times as long. `days` counts from the last use, or from `valid_from` if the note was never used. This is the Ebbinghaus forgetting curve with the spacing effect (every use slows the fading), which is established, not new.

Strength only affects `rank=weighted`, where it replaces the old fixed 0.5 in its 0.10 share. Full text is the default ranking and ignores it. So by default, use and age change nothing about what `recall` returns.

## Merging similar notes

```bash
throughline consolidate --dry-run               # list groups, change nothing
throughline consolidate --dry-run --json
throughline consolidate --dry-run --min-similarity 0.95
throughline consolidate --apply                 # merge every group
```

Candidates are notes that are valid now, have a vector, belong to the same project and have a cosine similarity of at least the active model's threshold, 0.92 for the default `nomic-embed-text` and 0.93 for granite (`--min-similarity`). Notes that are linked by such pairs form one group. The command needs the embedding model. Run `throughline embed` first.

A merge creates a new note with the longest text of the group. Its `source_type` is `consolidation` and `merged_from` holds the source ids. The sources become superseded and their text never changes. A forgotten note stays forgotten. A star on a source moves to the new note. `--apply` exits non-zero if any group failed; the other groups are still merged.

`reflect --mode dedup` merges through the same path (new note, sources superseded, texts unchanged), but it groups only notes with the same project, the same category and an identical first 80 characters. It does not use the cosine. Similar but different notes are found by `consolidate` and the Review tab. The action in the UI reports how many groups it merged and how many failed.

Over HTTP:

- `GET /api/consolidation/candidates?min_similarity=` lists the groups.
- `POST /api/consolidate` merges one group of ids. 422 means the request is not valid, 404 that a note does not exist, 409 that a note is no longer current.
- `GET /api/consolidation/proposal` asks a local model for a merged text. It never uses a cloud model. If the model fails or is missing, it falls back to the longest note. The UI waits 120 seconds for it.
- `GET /api/damaged-notes` lists current notes the old reflect glued together: the text holds a `---` separator line and the note lists itself in `merged_from`. Replace lets you write the clean text.

Automatic choice of the generation model never picks a cloud model, that is a name ending in `:cloud` or a model served from a remote host. A model whose name ends in `:cloud` or `-cloud`, and any Ollama backend on ollama.com, is never treated as local. Name one explicitly if you want it.

Privacy: by default nothing is sent to a model that is not local. These commands send transcript or note text to the generation model: `extract-memory` (also the hourly job, when you enable it), `generate-titles`, the contradiction check of `reflect` (`--mode contradictions`, and the model step of `reflect` without a mode), `ask` (also `/api/ask`) and `answer-eval` (also its HTTP route). If the model is not local, they stop with an error that names `THROUGHLINE_ALLOW_REMOTE_LLM`, before any text is sent (over HTTP: 403 `remote_llm_not_allowed`). An Ollama server counts as local wherever it runs, also on another machine, unless the model is a cloud model (a `:cloud` or `-cloud` name) or the server is ollama.com or a subdomain of it. An OpenAI-compatible endpoint counts as local only on this machine: the host must be `localhost`, an address in `127.0.0.0/8`, `::1` or `host.docker.internal`. The host is compared exactly, so `localhost.example.com` is not local. To allow a remote model anyway, set `THROUGHLINE_ALLOW_REMOTE_LLM=1` (`true`, `yes` and `on` also work). The merge proposal sends text to a model that is local by the same rule only, even with that setting, and otherwise offers the longest note.

## Notes and the time they were valid

A note has two clocks. `created_at` says when Throughline recorded it. `valid_from` and `valid_to` say when the statement was true. Where a note starts depends on how it came in. A note pinned in the UI or written through MCP is valid from now. An extracted note starts at the `source_at` of its conversation window. An agent-memory note starts at the modification time of its file, never later than now. A replacement starts where you say, and otherwise as described below. Notes that existed before migration 012 start at their source date, or at their creation date. `valid_to` stays empty until something ends it.

Replacing a note (`POST /api/memory/{id}/supersede`, or the MCP `supersede` tool) takes two optional fields:

- `kind`: `update` (the default) means the old note was right until now. It stays valid until the new one starts, and the new one starts now unless you give a date. `correction` means the old note was wrong from the beginning. Its interval is emptied, and the new note starts where the old one started.
- `valid_from`: the start of the new note, as RFC 3339 or `YYYY-MM-DD`.

A start in the future is refused with 422 `valid_from_in_future`. A start before the note being replaced is refused with 422 `valid_from_before_predecessor`. A plain date on the day the old note began counts as that note's start, so "from today" works for a note made today. A star on the old note moves to the new one.

To ask what held on a given day, use `GET /api/memory?valid_at=2026-01-15`. To ask what Throughline knew on that day, use `known_at`. Both take RFC 3339 or `YYYY-MM-DD`, and either one replaces the `status` filter. A plain date means the end of that day in UTC (at most now), so a question about today includes what changed today. A note counts as valid until the earlier of `valid_to` and `expires_at`. Forgotten notes never appear in these answers. A date that does not parse gives 422 `validation_error` and names the field.

Only replacing and merging end a validity. Forgotten and stale notes keep an open `valid_to`, because nobody said the statement stopped being true. `POST /api/memory/{id}/keep` brings such a note back and clears `expires_at`. If the note has a successor, keep answers 409 `has_successor`, since reviving it would put two answers side by side.

The agent-memory import follows the same rules. A changed section becomes a successor through update, starting at the file's modification time (never later than now). A section that vanished from the file is marked superseded with an end date. Nothing is deleted. If you replaced an imported note in the UI or over MCP, the import keeps your version as long as the file section stays as it was. Once the section changes, its new text replaces your version, because the newer change wins.

Replacing a forgotten note keeps it forgotten. It points at its successor and gets an end date, but it shows up neither in History nor in an answer about a date. The import leaves a forgotten note forgotten when its section vanishes.

## Recall: finding notes by meaning

`recall` finds memory notes for a question and says why each one came up. By default it ranks by full text. The weighted ranking, which also weighs meaning, recency and reliability, is there on request and needs embeddings. `throughline embed` now embeds memory notes (every status except forgotten) before it turns to messages, so run it once after upgrading.

```bash
throughline recall "how do we deploy" --project demo --mode current --limit 5
throughline recall "how do we deploy" --rank weighted
throughline recall "deployment target" --mode as_of --at 2026-01-15 --json
```

Over HTTP: `GET /api/recall?q=docker&project=demo&mode=current&limit=5`. The other parameters are `rank` (`fulltext` or `weighted`), `valid_at` and `known_at` (RFC 3339 or `YYYY-MM-DD`). A mode or rank that does not exist, a date that does not parse, `as_of` without a date, or a question longer than 2,000 characters gives 422 `validation_error`. Over MCP, the `recall` tool takes `query` (required), `project`, `mode`, `rank`, `valid_at`, `known_at` and `limit`; bad values give error -32602. The MCP `search` tool also honours `project` now.

Full text is the default because the first measurement (below) did not show the gain for the weighted ranking that the bar set beforehand asked for, and that bar came with the rule that the simpler alternative then becomes the default. Full text here means `ts_rank` of the question's words, joined by OR: notes that share words come first, the others follow by id. Pass `rank=weighted` (CLI `--rank weighted`) to rank by the weighted sum instead. Either way, `why` lists the weighted parts, and `why.fulltext` holds the `ts_rank` when full text ranked the hits.

The four modes differ in what they admit and how they rank:

- `current` (the default) admits notes that hold now: not replaced, merged, forgotten or expired, from the given project and from notes without a project. It ranks by full text unless you ask for `rank=weighted`, a weighted sum of cosine similarity (0.45), word overlap (0.20), recency (0.10, a decay with a 90-day scale), reliability (0.10), strength (0.10, from use and age, see above) and provenance (0.05).
- `as_of` needs `valid_at` or `known_at`. It admits notes that held on that day and match the question by words or by cosine of at least the model's threshold (0.5 for the default `nomic-embed-text`, 0.68 for granite, see "Embedding model"), keeps the `limit` best of them by the rank, and sorts those by `valid_from`. It shows the chain fields, so you see what replaced what.
- `associative` takes the best current notes by the rank as seeds and adds neighbours through a replacement chain, the same conversation, the same file, or a merge, with at most two hops. A neighbour scores max(its own score, seed score × 0.8^hops); its own score uses the same rank as the seeds. A replaced neighbour is labelled "superseded", one whose validity or expiry has passed or that is stale is labelled "expired". It returns up to twice the limit.
- `grounded` admits only notes whose chain started in a conversation that still exists, and attaches the source message that matches best (by cosine, by words if there is no vector). It walks the ranking a few notes at a time and stops once the limit has evidence. That checks a note against Throughline's own transcripts, not against the world.

Each hit carries `source_at`, the date of its source: the source date of the first note in its chain, else the start of its conversation. The CLI prints it after the session title.

Without Ollama no vector exists for the question. Full text in `current` does not need one and answers normally. The weighted ranking, `as_of` and `grounded` answer with `"degraded": "no_embeddings"` and rank by words only. In the memory view, "By meaning" uses the weighted ranking and shows the same notice.

### Measuring recall

`throughline recall-eval --set <file> --k 5 [--seed 7] [--json]` runs a set of questions with known answers through `current`, `weighted`, plain cosine, full text, phrase match and random, and reports hit@k and the mean reciprocal rank (MRR). `current` uses the default rank, so it equals `fulltext` by construction; `weighted` is the weighted ranking. The set is a file you write from your own notes. Keep it outside the repository if the notes are private.

A first test on 30 questions gave this: the weighted ranking hit 66.7 % at k = 5 with MRR 0.62, full text 66.7 % with MRR 0.53, cosine 63.3 % with MRR 0.57, random 3.3 %. So the weighted ranking did not beat plain full text on hits. It ranked the right note higher on average, but the bar set beforehand was five points over the better alternative, and that was not reached. Following the rule that came with the bar, full text became the default. Thirty questions from one corpus is a small sample; a larger measurement will look at the weighted ranking again.

## Embedding model

Recall by meaning, `consolidate` and the `as_of` mode all work on vectors from one embedding model. The default is `nomic-embed-text` (768 dimensions), an English-first model, so an existing install keeps its model and its vectors on restart.

The measured recommendation for notes in more than one language is granite (768 dimensions). It is opt-in: with it, a German question can find an English note and the other way round. That happens wherever vectors rank: "By meaning" in the memory view and `--rank weighted`, the `as_of` and `grounded` modes, semantic search and `ask`. The default recall (`current` with full text) ranks by shared words, so with any model it finds a note in another language only through words both share, such as names.

Granite comes under two names, and both get the same profile:

- `granite-embedding:278m` from the Ollama library: `ollama pull granite-embedding:278m`. Not `granite-embedding:30m`, which is an English model with another dimension and has no profile.
- `granite-embedding-multilingual:278m`, imported from Docker Hub when `ollama pull` fails (see below).

The thresholds were measured on the Docker Hub file (562,765,696 bytes, digest `984f398c…`). The Ollama library file (562,765,632 bytes, digest `a658c903…`) is the same model family and quantisation, but not byte-identical, so its scores can differ slightly. To use granite, fill its vectors first and then switch, as described under "Switching models without losing recall".

Each model has a profile in `rust/crates/throughline/src/embedding.rs`: prefixes, dimensions, column and two thresholds. Throughline adds the prefix itself, to questions and to documents, so you never put it in the text.

| Model | Question prefix | Document prefix | as_of cosine | consolidate cosine |
| --- | --- | --- | --- | --- |
| `nomic-embed-text` (default) | none | none | 0.5 | 0.92 |
| `granite-embedding-multilingual`, `granite-embedding:278m` | none | none | 0.68 | 0.93 |
| `embeddinggemma` | `task: search result \| query:` | `title: none \| text:` | 0.5 | 0.92 |
| `nomic-embed-text-v2-moe` | `search_query:` | `search_document:` | 0.5 | 0.92 |
| `qwen3-embedding` (1024 dimensions) | an `Instruct:` line | none | 0.5 | 0.92 |

Only the first two rows were measured for their thresholds. The other three profiles carry the baseline values. `qwen3-embedding` is refused for now: it needs a 1024-dimension column, and no migration adds one yet. A model without a profile is probed once with a real embed call and gets no prefixes and the baseline thresholds. It works only if it returns 768 or 1536 dimensions.

Vectors are keyed by the exact model string. Switching models never overwrites or deletes anything: the old vectors stay in `embeddings`, and each command reads only the vectors of the active model. Notes count as candidates when they have no non-null vector for that model and are not forgotten. Replaced and merged notes are embedded too, because `as_of` and `associative` recall read them.

`throughline consolidate --min-similarity` now defaults to the active model's threshold (0.92 for the default `nomic-embed-text`, 0.93 for granite). The `as_of` mode admits a note by words or by the model's cosine threshold.

### Switching models without losing recall

A new model has no vectors at first, so switching at once would leave recall empty until `embed` caught up. Fill the new model beside the old one, then switch:

1. Set `THROUGHLINE_EMBED_TARGET_MODEL` to the new model in `.env` and run `docker compose up -d web`.
2. Let `throughline embed` run. It fills the active model first, then the target. The hourly job does this on its own. If the target model cannot be used, `embed` prints `target model: <error>` and still exits 0.
3. Run `throughline doctor` until it prints `embedding target: <model>: complete, set OLLAMA_EMBED_MODEL to it, remove the target and run docker compose up -d web`. While it runs you see `embedding target: <model>: notes <done>/<total>, messages <done>/<total>`.
4. Set `OLLAMA_EMBED_MODEL` to the new model.
5. Remove `THROUGHLINE_EMBED_TARGET_MODEL` and run `docker compose up -d web`.

Use `docker compose up -d web`, not `docker compose restart web`: a restart keeps the container's old environment, so the change in `.env` would not reach it.

`doctor` also prints `embedding missing:` for the active model: `complete`, or how many notes and messages still have no vector. Both variables are in `.env.example`, and `docker-compose.yml` forwards both to the `web` service. A large corpus takes hours to embed, so do step 2 in the background.

Write the model string identically everywhere: the same string for the target and, after the switch, for the active model, and the same string in every process that reads or writes vectors, that is `web`, the native CLI and the MCP server over stdio. Vectors are keyed by the exact string, so `granite-embedding:278m` and `granite-embedding-multilingual:278m` are two sets of vectors although they share a profile, and so are `nomic-embed-text` and `nomic-embed-text:latest`.

### When `ollama pull` fails

Behind a proxy `ollama pull` can fail. The same GGUF files are published on Docker Hub under `ai/` as Docker Model Runner artifacts; for granite that is `ai/granite-embedding-multilingual:278M-F16`. Fetch the model layer (media type `application/vnd.docker.ai.gguf.v3`) through the registry API with a pull token, check its sha256, then import it:

```bash
printf 'FROM ./model.gguf\n' > Modelfile
ollama create granite-embedding-multilingual:278m -f Modelfile
```

Then use `granite-embedding-multilingual:278m` as the model name, in `OLLAMA_EMBED_MODEL` and in the target.

### What the measurement showed, and its limits

On a copy of one real corpus (1,591 notes, 60 questions, testset v4), the English baseline `nomic-embed-text` found the right note across languages in 8 % of the cases at k = 5 and in 92 % within one language. `granite-embedding-multilingual:278m` (the Docker Hub file) reached 88 % and 100 %. Embedding a question took about 9 ms. `embeddinggemma` scored the same 88 % and 100 %; granite won on the rule set beforehand by a mean reciprocal rank across languages of 0.665 against 0.657, and other readings of the tie rule flip that choice.

Read the gap with care. 18 of the 25 cross-language questions were translated for the testset and share no word with their note, which favours a multilingual model more than daily use does. On the other 7, taken over unchanged, the baseline found 2 and granite all 7. Each cross-language group had 25 questions, so the intervals are wide. A first run on an earlier testset was invalid because identifiers appear in every language and full text alone reached 52 %.

Granite reads at most 512 tokens. Longer text is cut silently by Ollama: 2 of the 1,591 notes and about 7 % of messages in a sample. For `nomic-embed-text`, `ollama show nomic-embed-text` lists a context length of 2,048 tokens.

## Context pack

`context` builds a briefing for one project that fits a token budget: what a new session should know before it starts. Where `handoff` carries one session forward, the context pack reads the project's current notes. It needs no Ollama.

```bash
throughline context --project demo
throughline context --project demo --task "fix the login redirect" --budget 1200
throughline context --project demo --json
```

Over HTTP: `GET /api/context?project=demo&task=…&budget=1200`. Over MCP, the `project_context` tool takes `project` (required), `task` and `budget`. The answer holds `text` (the briefing), `items` (each line with its section, token cost and citation), `citations`, `tokens_used`, `budget`, `within_budget`, `skipped` and `estimate`.

### What goes in, and in which order

Notes that hold now are used: not replaced, forgotten, merged or expired, from the project plus notes without a project. A project name that does not exist is not an error: the pack then holds the header and any notes without a project, not an empty pack. Sections appear in this order and only when they have a line.

1. **Decisions**: current notes of the categories decision, preference and project_context, newest first; with a task, the ones that match it by full text come first, best match first. They come before everything else, so they are the last to be crowded out. Stale notes never appear here, only under Needs review.
2. **Relevant to the task**: only with a task. Up to 20 notes ranked by full text, without the ones already shown.
3. **Recent sessions**: the 8 newest human sessions of the project (conversations generated by Throughline itself are left out).
4. **Open questions**: up to 5 notes that contain a question mark, "todo" or "open".
5. **Needs review**: up to 5 stale notes, or notes whose validity lapsed without a successor.
6. **Sources**: every session cited by a line that made it in, including the recent sessions above.

### The budget

The budget is in estimated tokens, 64 to 32,768, default 2,000. The estimate is the larger of the word count and the character count divided by four (rounded up), at least 1, plus 1 for each line break. A section heading costs the same as a line. Decisions come first, so in a project with many of them a 2,000-token pack may hold only decisions; with a `task` the ones that match it lead, so they are the ones that fit, but a larger budget still holds more. Throughline has no tokenizer for your model, so this is a rule of thumb; leave some room if the limit is tight.

The pack is filled line by line in the order above. A line that would push the total over the budget is skipped whole, and a shorter line after it can still fit. Lines are never cut. `skipped` counts the lines left out. `tokens_used` never exceeds `budget`. A budget below 64, or above 32,768, gives 422 `validation_error` over HTTP and error -32602 over MCP. A header that does not fit the budget (a very long project name) gives the same, 422 `validation_error` or -32602, and a plain error on the command line.

### Line format

The first line is the header: project, date and budget. A note reads `- [m#42 · 2026-03-02 · decision] text (source: session "Title", 2026-03-02)`, where `m#` is the note id and the part in parentheses is left out for a note that does not come from a conversation. A session reads `- [s#17 · 2026-03-02 · claude_code] Title`. Line breaks inside a note become spaces. Session titles are shown on one line and cut to 120 characters, with "…" when cut. A source session is dated by its start. A note's source session can belong to another project; its title is shown all the same. Every `m#` and `s#` id exists in the database; `citations` lists each one once.

### Example

Invented data, budget 400:

```text
Context for Atlas-demo · 2026-10-30 · budget 400 tokens (estimated)
## Decisions
- [m#118 · 2026-10-02 · decision] Deploy with Docker Compose on the staging host. (source: session "Pick a deploy target", 2026-10-02)
- [m#97 · 2026-09-21 · preference] Keep migrations small and reversible.
## Recent sessions
- [s#31 · 2026-10-28 · claude_code] Fix the login redirect
## Open questions
- [m#121 · 2026-10-05 · insight] Should the export job run nightly? TODO
## Sources
- [s#12 · 2026-10-02] "Pick a deploy target"
- [s#31 · 2026-10-28] "Fix the login redirect"
```

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

**MCP.** The Rust server speaks the Model Context Protocol over stdio
(`throughline mcp`) and over HTTP (`POST /mcp`), so any MCP-capable client can
query the shared store mid-session. Both transports run the same code and offer
the same tools. The next section covers the HTTP side and the tool list. The
older Python server, `memory_mcp`, has a different tool contract; see
[`memory_mcp/README.md`](../memory_mcp/README.md).

**SessionStart hook.** `throughline install-hooks` writes a hook into
`~/.claude/settings.json` that drops a short, project-scoped summary of past
decisions and preferences into each new session — context without asking for
it.

## Agent access over HTTP

`POST /mcp` is MCP over Streamable HTTP. It answers with plain JSON: no SSE stream and no sessions. `GET` and `DELETE` on `/mcp` answer 405. A request body may be 1 MiB at most (413 above that), and a JSON batch (an array) is refused.

### Turn it on

`/mcp` is off until you set a key.

```bash
throughline agent-key        # prints a fresh 64-character hex key, writes nothing
```

Put it into `.env` as `THROUGHLINE_AGENT_KEY=<key>` and restart the web container. The key must have at least 32 characters after trimming. Unset or shorter, `/mcp` answers 503 `agent_access_off` and the UI keeps working. A missing or wrong key gets 401. `throughline doctor` prints `agent access: on` or `agent access: off: <reason>`, and the doctor report of `GET /api/health-recovery` carries the same text in its `agent_access` field. Neither shows the key. `THROUGHLINE_API_KEY` stays the key of your model provider and is not involved.

### Set up a client

Claude Code:

```bash
claude mcp add --transport http throughline http://127.0.0.1:8788/mcp \
  --header "Authorization: Bearer $THROUGHLINE_AGENT_KEY"
```

Your shell fills in `$THROUGHLINE_AGENT_KEY` when you run `claude mcp add`, so export the key in that shell first (or paste the key in place of the variable). Putting it into `.env` alone is not enough.

Port 8788 is the Compose default (`THROUGHLINE_WEB_PORT`). Run the web server natively and the port is the one you started it on, 8787 by default.

Codex reads MCP servers from `~/.codex/config.toml`. A Streamable HTTP server takes a `url` and, for a bearer token, `bearer_token_env_var`, the name of an environment variable that holds the key:

```toml
[mcp_servers.throughline]
url = "http://127.0.0.1:8788/mcp"
bearer_token_env_var = "THROUGHLINE_AGENT_KEY"
```

Codex reads that variable from its own environment, so `THROUGHLINE_AGENT_KEY` must be set in the environment Codex runs in.

Those keys come from the Codex MCP documentation (read on 2026-10-30), not from a run against Throughline. Any other client needs the same two things: the URL and an `Authorization: Bearer <key>` header.

### Tools

| Tool | What it does | Counts as use |
| --- | --- | --- |
| `search` | Keyword search over messages. Arguments: `query`, `limit`, `project`. | no |
| `recall` | Memory notes with the reason for each hit. Modes `current`, `as_of`, `associative`, `grounded`; rank `fulltext` or `weighted`. | yes |
| `project_context` | The context pack of a project (`project`, `task`, `budget`). | yes |
| `get_note` | One note by `id` with its source and its whole supersede chain, oldest first. A forgotten note is not found, and forgotten notes leave the chain. | yes |
| `conflicts` | The conflict report, optionally for one `project`. Each row has a kind: `contradiction` (open), `documented_supersession` (already resolved by a replacement) or `stale_tool_drift` (never-used notes of one tool). | no |
| `list_projects` | Known project names. | no |
| `stats` | Corpus counts. | no |
| `write` | A new note. `category` is one of the allowed categories (use `decision` for a decision); `project` is optional. | no |
| `forget` | Marks notes forgotten (`ids`). See [Keeping memory trustworthy](#keeping-memory-trustworthy). | no |
| `supersede` | Replaces a note with a new active one; the old note stays as history. | no |
| `keep` | Confirms a note: active again, expiry cleared. A note that a newer note replaced stays superseded. | yes |

### Resources and prompts

Resources: `throughline://projects`, and the templates `throughline://project/{name}/context` (the context pack with the default budget) and `throughline://note/{id}`. A forgotten note is not found, as with `get_note`. Reading a note or a context pack counts as use; `resources/list` does not.

Prompts: `before_coding(project, task?)` and `handoff(project)`. Both fetch the context pack and put it inside a `<throughline-data>` block, as data and not as an instruction. In Claude Code they show up as slash commands. Getting a prompt counts as use.

### Protocol versions and errors

The server speaks the modern version `2026-07-28` (per-request `_meta`, the headers `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name`, and `server/discover`) and the older `initialize` handshake with `2025-11-25` and `2025-06-18`. Over stdio `2024-11-05` also works. `2025-03-26` is not supported. Modern results carry `"resultType": "complete"`. No session id is issued.

| Code | Meaning |
| --- | --- |
| `-32020` | Header missing or different from the body (HTTP 400). |
| `-32022` | Unsupported modern version (HTTP 400, `data` lists `supported` and `requested`). |
| `-32601` | Unknown method (HTTP 404 in the modern era, 200 in the legacy one). |
| `-32602` | Invalid parameters, an unknown tool or one that read-only mode blocks; in the modern era also a resource that does not exist. |
| `-32002` | Resource not found, legacy era. |
| `-32700`, `-32600` | Body is not JSON, or is a batch. |
| `-32000` | Operation refused, for example a remote embedding model without `THROUGHLINE_ALLOW_REMOTE_LLM`. |

A request with a foreign `Origin` gets 403 even with the right key.

### Read-only mode

`THROUGHLINE_MCP_READ_ONLY=1` (also `true`, `yes`, `on`) removes `write`, `forget`, `supersede` and `keep` from `tools/list` on stdio and on HTTP. Calling one answers `-32602`, like any unknown tool. Reading still counts as use, because that is a statistic and not a change to a note.

### Embeddings and the remote-model lock

Embeddings follow `THROUGHLINE_ALLOW_REMOTE_LLM` too. An Ollama host at ollama.com or a cloud embedding model is refused unless the variable allows it. `GET /api/recall` then answers 403 `remote_llm_not_allowed`, and MCP answers `-32000`.

### What the key does not do

Two limits, stated plainly.

- What an agent recalls lands in the context of that agent's model. `THROUGHLINE_ALLOW_REMOTE_LLM` governs Throughline's own model calls and does not govern that. If the agent runs on a hosted model, the notes it reads go there. This is the same over stdio.
- The key protects `/mcp`, and it marks agent use on `GET /api/recall` and `GET /api/context`. It does not protect the UI or the rest of the API, because the UI calls `/api` from the browser without a key. It does not protect against other programs of the same user either: they can call `/api` and read `.env`.

Throughline writes no log of MCP requests, keys or client names.

## VS Code

The extension in [`vscode/`](../vscode/README.md) brings recall, the context pack, single notes and the conflict report into VS Code, and writes notes from the editor. It calls only the address in `throughline.url`.

Looking goes through `/api` without a key and does not count as use. Writing and handing the context pack to Cline or Continue go through `POST /mcp` with the agent key, which VS Code keeps in its secret storage. Only the pack counts, as `project_context` does above. Without Cline or Continue the pack lands on the clipboard.

There is no marketplace release. Build the VSIX with `npm ci && npm run package` in `vscode/`, install it with `code --install-extension`, then run **Throughline: Set Agent Key**. Commands, settings and the hand-over are in the [extension README](../vscode/README.md).

## Team mode

When your server runs with `THROUGHLINE_AUTH_MODE=team`, you have an account on it. Setting it up, roles and limits are in [TEAM_DEPLOYMENT.md](TEAM_DEPLOYMENT.md). Day to day it looks like this:

- **Sign in.** The page opens with a sign-in form: name and password from whoever runs the server. A session lasts seven days and then asks again. The top bar shows your name, your role, "Change password" and "Sign out".
- **Change your password.** "Change password" asks for the current one and the new one twice (15 to 128 characters). Your other sessions end, this one stays. A forgotten password is reset by the operator with `throughline account password <name>`.
- **Private notes.** When you pin a note, tick "Private: only I can see this note". Nobody else sees it, admins included. Without the tick a note is shared. Over MCP, `write` takes `"visibility": "private"`. A note cannot be made private later, and keeping or superseding a note does not change who sees it.
- **What your role allows.** A viewer reads. An editor also writes, keeps, forgets and supersedes notes, sets stars and asks the model. An admin also runs jobs and sees the AI configuration. Buttons you may not use are hidden.
- **Agents and VS Code.** The global `THROUGHLINE_AGENT_KEY` does not work in team mode. Ask the operator for a key of your account (`throughline account agent-key <name>`). Claude Code, Codex and Cline send it as `Authorization: Bearer <key>`, exactly as in [Agent access over HTTP](#agent-access-over-http). In VS Code, store it with **Throughline: Set Agent Key**: the extension retries a 401 once with the key, so recall and the context pack work, and they count as use. The agent sees and writes what your account sees and writes.

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
