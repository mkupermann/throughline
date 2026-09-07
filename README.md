<p align="center"><img src="docs/brand/wordmark.svg" alt="Throughline — Your AI work, in context" width="100%"></p>

[![License: MIT](https://img.shields.io/badge/license-MIT-151b1e.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-151b1e.svg)](pyproject.toml)
[![PostgreSQL 16 + pgvector](https://img.shields.io/badge/postgres-16%20%2B%20pgvector-151b1e.svg)](sql/schema.sql)
[![Status: beta](https://img.shields.io/badge/status-beta-c55234.svg)](CHANGELOG.md)

**Open a project after four weeks. Understand what you investigated, why the decisions changed, which results still hold, and what comes next.**

Throughline brings locally stored AI conversations into a source-linked project history. It imports sessions from nine supported tools into PostgreSQL, keeps prompts and answers in their conversations, and helps you resume work without assembling the story from separate archives.

The core workflow works without a connected AI model. Optional models add semantic search, extraction and generated answers. Throughline is currently a local, single-user application; it is not an authenticated team service.

[Start locally](#quick-start-with-docker) · [Watch the walkthroughs](#see-every-area) · [Try fictional data](docs/DEMO.md) · [Data-model audit](docs/PROJECT_STORY.md) · [What is still missing](docs/ROADMAP.md)

## Follow the work

1. **Choose a project.** See its recorded goal, current position, blocker and next step, with links to the messages supporting them.
2. **Follow its conversations.** Browse across tools and dates. Expand a conversation to read the full messages and their extracted notes.
3. **Inspect the evidence.** Prompt, answer and recorded output have distinct labels. Timestamps include seconds and timezone when available. An absent timestamp or unrecorded file is shown as missing.
4. **See what changed.** Earlier project notes remain available. A superseded memory points to its replacement and source conversation.
5. **Prepare the next session.** Select conversations, inspect a compact Markdown handoff, then download it for another tool.

![Atlas project: a source-linked goal, current position and next step](docs/videos/projects.png)

The collapsed conversation card previews the **first prompt**, **last recorded answer**, and **latest recorded tool output or file-reference count**. These are explicitly labelled excerpts, not an inferred prompt/answer pairing. Open the conversation to inspect the sequence. A tool output is not automatically proof that a file was successfully created; explicit file blocks are shown as references, without claiming the file still exists.

## A project is the context

Conversations belong in projects. Messages and extracted memories belong in their conversations. A message may be a search hit or a direct source link, but it is not an independent event on the Timeline.

Project grouping currently derives from source working-folder names. Matching names can include multiple folders; the project view exposes those folders and lets you filter them. Persistently correcting that grouping is still planned.

Time order describes chronology, not causality. Throughline displays supported explicit source relationships where recorded; it does not infer a dependency because two sessions happened close together. See the [relationship and provenance audit](docs/PROJECT_STORY.md).

## See every area

The demo seeder reseeds its target tables; use a separate database ending in `_demo`. Its optional `--reset` flag drops that demo database.

Each link opens a short MP4 recorded from the actual interface using the bundled fictional corpus. Videos have burned-in captions and separate WebVTT tracks. They demonstrate navigation and inspection; no real model call or agent execution is presented as a demo result.

| Area | What the clip demonstrates | Video |
|---|---|---|
| **Projects / Projekte** | Find Atlas and recover its source-linked state | [Watch](docs/videos/projects.mp4) |
| **Conversations** | Choose a project, open a conversation and inspect its output reference | [Watch](docs/videos/conversations.mp4) |
| **Find** | Search imported records with their source context | [Watch](docs/videos/find.mp4) |
| **Timeline** | Compare activity across tools and open a dated bucket | [Watch](docs/videos/timeline.mp4) |
| **Review** | Inspect contradictory and superseded knowledge | [Watch](docs/videos/review.mp4) |
| **Operate** | Understand import, extraction and embedding stages | [Watch](docs/videos/operate.mp4) |
| **Console** | Count conversations by source with read-only SQL | [Watch](docs/videos/console.mp4) |
| **AI-team operations / KI-Teamsteuerung** | Inspect linked projects, task states and budgets | [Watch](docs/videos/teams.mp4) |
| **Roles** | Separate analysis, execution, testing and review responsibilities | [Watch](docs/videos/roles.mp4) |
| **Members** | Inspect fictional people and agents | [Watch](docs/videos/members.mp4) |
| **Team pipelines** | Inspect reusable team configuration | [Watch](docs/videos/pipelines.mp4) |
| **Model providers** | Inspect provider configuration independently of project history | [Watch](docs/videos/models.mp4) |

[Video gallery and captions](docs/videos/README.md) · [Reproduce the recordings](docs/DEMO.md)

The navigation keeps daily work separate from system operations. Labels remain visible in narrow panels. Use `Ctrl+K` / `Cmd+K` for the command palette; `g` then `v` opens Conversations. Project history is the default project view; **Full document** opens the alternative complete document layout.

## Know what you can trust

| What you see | What it means |
|---|---|
| A project-state note | A user-recorded statement with a source; it is not independent verification |
| Extracted memory | A model-derived note to inspect in its conversation |
| A replacement or supersession | The earlier statement remains traceable; status alone does not establish truth |
| A generated answer | A model response whose citations need checking |
| A file reference | An explicit recorded output block; current file availability is not verified |
| A missing relationship or timestamp | The imported data did not establish it |

Counts describe imported records, not every conversation that ever existed. Unsupported formats, missing files, source timestamps and excluded automation can limit coverage. The project view exposes its source folders, refresh time and automation filter. The latest state fields remain separate from the last 100 historical state notes.

## Quick start with Docker

Docker Compose is the shortest supported path. It includes PostgreSQL 16 with pgvector and serves the app on loopback.

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
python3 scripts/init_compose_env.py
docker compose up -d
docker compose exec web throughline ingest --all
```

On Windows, use `py -3 scripts/init_compose_env.py` if `python3` is not available.

Open [http://127.0.0.1:8788](http://127.0.0.1:8788).

The initializer detects Cline’s task directory for macOS, Linux or Windows. For another editor profile, set `THROUGHLINE_CLINE_DIR` in `.env` after initialization to its task directory. Other source mounts are listed in `docker-compose.yml`.

The setup script creates an ignored `.env` with a random database password. Source directories are mounted read-only. The web API binds to loopback because it has no authentication. The first ingestion is explicit.

### Keep the database safe

The PostgreSQL named volume contains the corpus. Rebuilding or replacing the web container does not remove it.

Do not run `docker compose down -v` unless you intend to destroy the database. Use the normal update path instead:

```bash
git pull
docker compose build web migrate
docker compose up -d migrate web
docker compose exec web throughline doctor
```

Create a verified backup before a major update:

```bash
docker compose exec web throughline backup
```

See [Deployment](docs/DEPLOYMENT.md) for upgrades, credential rotation, backups, and recovery.

## Local models

Embeddings enable semantic search. A generation model powers Ask, extraction, titles, and reflection. These are different jobs and need different models.

```bash
docker compose --profile embeddings up -d ollama
docker exec throughline-ollama ollama pull nomic-embed-text
docker exec throughline-ollama ollama pull qwen3.5:9b
docker compose exec web throughline embed --backend ollama
```

Use a smaller or larger generation model to match the machine. Throughline inspects the models Ollama actually has. `throughline doctor` reports what will run.

Provider keys entered in AI-team operations are currently stored in the local database as plaintext. Database access and backups must therefore be treated as credential access. Environment-based model keys and team-provider keys are separate configuration paths; see [Security](SECURITY.md).

Model use is an explicit privacy boundary:

| Operation | Local when |
|---|---|
| Embeddings | `--backend ollama` is selected, or `auto` runs without `OPENAI_API_KEY` |
| Ask | the resolved generation backend is local |
| Extraction, titles, and reflection | the resolved generation backend is local |

Embedding `auto` uses hosted OpenAI when `OPENAI_API_KEY` is present. Generation `auto` prefers a reachable local Ollama model, then a configured OpenAI-compatible endpoint, then hosted OpenAI. Set the backend explicitly when content must stay on the machine.

## Supported sources

| Tool | Session location |
|---|---|
| Claude Code | `~/.claude/projects/` |
| Cline | the editor's `globalStorage` task directory |
| Codex CLI | `~/.codex/sessions/` |
| Continue | `~/.continue/sessions/` |
| Cursor | `~/.cursor/sessions/` |
| Hermes | `~/.hermes/sessions/` |
| Vibe | `~/.vibe/logs/session/` |
| Windsurf | `~/.windsurf/plans/` |
| Zed | `~/.zed/data/sessions/` |

Adapters normalise each source into conversations and messages. Re-ingestion is idempotent. Changed source files refresh their stored conversation without creating duplicates. Third-party adapters can register through the `throughline.adapters` entry point. See [Adapter development](docs/ADAPTER_DEVELOPMENT.md).

Codex rollouts from current app builds and older CLI builds are both supported. Parser upgrades reconsider files that an older parser could not read.

## The daily loop

Most days Throughline should update itself in the background.

| Platform | Scheduler | Setup |
|---|---|---|
| macOS | per-user launchd agents | [`launchd/`](launchd/) |
| Linux | systemd user timers | [`systemd/`](systemd/) |
| Windows | Task Scheduler | [`windows/`](windows/) |

The scheduled jobs ingest hourly, extract daily, and back up daily. The Windows scripts detect a running Docker setup and use it directly. Native installations use the same commands with a local environment file.

When you need something back:

```bash
throughline ask "why did we change the ingestion queue?"
throughline search "pgvector index"
throughline serve
```

When you need to inspect the system:

```bash
throughline status
throughline doctor
throughline conflicts
throughline migrate --status
```

The complete command guide is in [Usage](docs/USAGE.md).

## Take the knowledge with you

Markdown export writes one folder per project. Sessions remain chronological and large projects split into manageable dated parts. Re-running updates files Throughline owns and leaves your own notes alone.

```bash
throughline export-markdown --out ~/Documents/Throughline
throughline export-markdown --out ~/Documents/Throughline --project throughline
throughline export-markdown --out ~/Documents/Throughline --redact
```

The redaction pass removes common key, token, email, and home-path shapes. It reduces exposure but cannot prove that arbitrary transcript content is safe. Review an export before placing it in a shared or cloud-synced folder.

The MCP server in [`memory_mcp/`](memory_mcp/) lets compatible clients search, recall, write, supersede, and forget shared memory while they work. The optional Claude Code SessionStart hook can preload a short project-scoped context file.

## Architecture

- Python and FastAPI provide the CLI, API, jobs, and server.
- React, TypeScript, Vite, and TanStack Query provide the web interface.
- PostgreSQL 16 and pgvector store the corpus and vector index.
- Ollama or an OpenAI-compatible endpoint can provide local generation.
- The built frontend ships inside the Python package. Installing Throughline does not require Node.

Schema changes use ordered migrations. Compose applies them before the web service starts. `throughline migrate --status` shows what is applied and what remains.

## Native installation

The native route is intended for a machine that already has PostgreSQL 16 and pgvector.

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
createdb throughline
throughline migrate
throughline ingest --all
throughline serve
```

The application reads standard `PG*` variables and an ignored repository-root `.env`. The native server defaults to [http://127.0.0.1:8790](http://127.0.0.1:8790).

Full setup details are in [Installation](docs/INSTALLATION.md).

## Optional AI-team operations

The system area can configure projects, roles, members, pipelines, providers and token budgets, and inspect supported local agent runs. Its project records are separate from imported-history projects and are connected through explicit links. A linked project name opens its conversation history.

This remains an advanced setup workflow. A guided path from project goal to team to first task, persistent project reassignment, multi-user permissions and broader user validation remain open. See [the roadmap](docs/ROADMAP.md).

## Development

Install the development dependencies, then run the same checks as CI:

```bash
pip install -r requirements-dev.txt
pytest tests/ -m "not integration" --ignore=tests/integration
ruff check throughline memory_mcp scripts skill/scripts evals tests
black --check throughline memory_mcp scripts skill/scripts evals tests
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
```

Integration tests require a disposable PostgreSQL 16 instance with pgvector. Current walkthroughs use [`scripts/seed_demo_data.py`](scripts/seed_demo_data.py). The older screenshot fixture remains in [`examples/demo_data.sql`](examples/demo_data.sql). See [demo reproduction](docs/DEMO.md) for the current media workflow.

Contributions are welcome. Read [Contributing](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report bugs in [Issues](https://github.com/mkupermann/throughline/issues). Report security problems through the channel in [Security](SECURITY.md).

## Status

Throughline is beta software. Its schema is migration-tracked and its core paths run in CI against PostgreSQL. Back up a corpus you care about. Treat every model boundary as a data boundary.

## License

Throughline is released under the [MIT License](LICENSE).
