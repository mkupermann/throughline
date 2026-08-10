# Throughline

![Throughline — one memory layer for every AI CLI on your laptop](docs/assets/hero.svg)

**Throughline is a universal, vendor-agnostic memory layer for AI coding assistants.** Every AI CLI forgets everything between sessions; Throughline ingests the session history of all major AI CLIs — Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, and Vibe — into one local PostgreSQL database and feeds that unified memory back to whichever tool you use next. Your sessions never leave your machine.

Switch providers freely — Anthropic today, Mistral or OpenAI tomorrow — and your accumulated context, decisions, and preferences move with you. The memory belongs to you, not to a vendor.

---

## Why Throughline

| Problem | Throughline's answer |
|---|---|
| Each AI CLI keeps its own siloed, ephemeral history | One normalised store across all nine supported tools |
| Vendor lock-in through accumulated context | Vendor-neutral schema; switch assistants without losing memory |
| Cloud memory features raise data-governance questions | 100% local: PostgreSQL on your machine, read-only source mounts |
| Raw transcripts are unsearchable | Structured memory chunks, semantic search (pgvector), knowledge graph |

A longer discussion of the cross-tool memory problem is in [docs/why-cross-tool.md](docs/why-cross-tool.md).

---

## Architecture

![Throughline universal architecture](docs/assets/architecture.svg)

Four layers: nine source adapters (one per AI CLI), a shared ingestion pipeline that normalises conversations, PostgreSQL 16 with pgvector for storage and semantic search, and the consumers that read memory back — the web UI, MCP server, CLI, and automation jobs.

### Data flow

![Throughline data flow](docs/assets/data_flow.svg)

### Ingestion sequence

![Universal session ingestion sequence](docs/assets/sequence_diagram.svg)

Design details: [docs/architecture.md](docs/architecture.md) · measured performance: [docs/BENCHMARKS.md](docs/BENCHMARKS.md)

---

## Supported tools

![Vendor integration matrix](docs/assets/vendor_matrix.svg)

| Tool | Vendor | Storage location | Format |
|------|--------|------------------|--------|
| Claude Code | Anthropic | `~/.claude/projects/<project>/*.jsonl` | JSONL transcripts |
| Codex CLI | OpenAI | `~/.codex/sessions/<date>/rollout-*.jsonl` | JSONL rollouts |
| Cursor | Anysphere | `~/.cursor/sessions/*.jsonl` | JSONL transcripts |
| Zed | Zed Industries | `~/.zed/data/sessions/*.json` | JSON transcripts |
| Vibe | Mistral AI | `~/.vibe/logs/session/session_*/` | Session directories |
| Hermes | Community | `~/.hermes/sessions/*.json` | JSON transcripts |
| Continue | Continue.dev | `~/.continue/sessions/*.json` | JSON transcripts |
| Cline | Cline | VS Code `globalStorage/.../tasks/` | Per-task files |
| Windsurf | Codeium/Cognition | `~/.windsurf/plans/*.md` | Markdown plans |

Adapters degrade gracefully: a tool that is not installed is simply reported as "not present". Third-party adapters plug in through the `throughline.adapters` entry point without any changes to Throughline itself — see [docs/ADAPTER_DEVELOPMENT.md](docs/ADAPTER_DEVELOPMENT.md).

---

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
docker compose up -d
# Web UI: http://127.0.0.1:8787
```

The compose stack starts PostgreSQL 16 with pgvector (schema applied automatically) and the web UI. The port is published on loopback only — the API has no authentication, by design for a single-user local tool. Host tool directories are mounted read-only into the container, so ingestion works out of the box. Optional profiles: `--profile mcp` (MCP server) and `--profile embeddings` (local Ollama for embeddings).

### Native installation

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
pip install -r requirements.txt
pip install -e .

createdb throughline
psql throughline < sql/schema.sql

throughline ingest --all
throughline serve
```

Full instructions, including scheduled ingestion via launchd/systemd: [docs/INSTALLATION.md](docs/INSTALLATION.md) and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Command line interface

```bash
throughline ingest --list-sources   # show all adapters and whether they are present
throughline ingest --all            # ingest from every present adapter
throughline ingest --source vibe    # ingest from one tool
throughline extract-memory          # distill structured memory chunks from conversations
throughline generate-titles         # title untitled conversations
throughline search "authentication" # semantic search across all tools' history
throughline reflect                 # self-reflecting memory maintenance (dedup, decay)
throughline status                  # database and ingestion status
throughline doctor                  # diagnose configuration problems
throughline serve                   # web UI + API on http://127.0.0.1:8787
```

Usage guide with examples: [docs/USAGE.md](docs/USAGE.md).

### The web UI

`throughline serve` runs the UI and its JSON API from one process on one port
(default `http://127.0.0.1:8787`). Five surfaces:

| Surface | What it is for |
|---|---|
| **Overview** | A worklist, not a dashboard: one headline number, one health verdict, then only the things that need attention. |
| **Find** | One query across conversations, messages, memory, skills, projects and prompts — lexical and semantic retrieval fused, with facets and four views (list, table, timeline, graph). |
| **Curate** | Eight queues that keep memory trustworthy — contradictions, drift, superseded chains, low confidence, missing embeddings, expiring, never accessed, forgotten. Bulk actions with undo. |
| **Operate** | Pipeline state and the jobs that change it, with live streamed output. |
| **Console** | Read-only SQL. Every statement runs in a `READ ONLY` transaction, so PostgreSQL rejects writes — not a keyword filter. |

Press `⌘K` for the command palette, `/` to search, `g` then `o f c p s` to jump
between surfaces. Light and dark both supported; the theme follows your system
by default.

---

## Technical deep-dive

### Adapter contract

Each adapter implements a deliberately small interface:

- `is_present` — does this tool's data directory exist here (cheap, no parsing)
- `discover()` — yield candidate conversation files
- `parse(path)` — convert one file into a `NormalisedConversation`

Everything else — database connections, idempotency via the ingestion log, project bucketing, error handling — lives in the shared writer, so adapters stay small and individually testable. Re-ingestion is idempotent: unchanged files are skipped, changed files are refreshed without duplicates.

### Storage and retrieval

- **PostgreSQL 16 + pgvector**: conversations, messages, and extracted memory chunks in a normalised schema (`sql/schema.sql`), with vector similarity search over embeddings and trigram search over content.
- **Memory extraction**: an LLM pass distills durable facts (preferences, decisions, error solutions, project context) from raw transcripts into typed, tagged memory chunks.
- **MCP server**: `memory_mcp` exposes the memory database to any MCP-capable client over stdio, so every supported CLI can query the shared memory at runtime.
- **Privacy**: sources are mounted read-only; nothing is transmitted off-machine unless you explicitly configure a cloud embedding or extraction provider. PII scanning utilities are included (`throughline/pii.py`).

Performance characteristics and tuning: [docs/PERFORMANCE.md](docs/PERFORMANCE.md) · security model: [SECURITY.md](SECURITY.md).

---

## Extending Throughline

To support a new AI CLI, implement the adapter contract and register it:

```python
from pathlib import Path
from typing import Iterable
from throughline.adapters.base import Adapter, NormalisedConversation

class MyToolAdapter(Adapter):
    name = "my_tool"
    label = "My Tool"
    home = Path("~/.my_tool/sessions").expanduser()

    def discover(self) -> Iterable[Path]:
        yield from sorted(self.home.glob("*.json"))

    def parse(self, path: Path) -> NormalisedConversation | None:
        ...
```

Register it either in `throughline/adapters/registry.py` (built-in) or via the `throughline.adapters` entry point in your own package (no core changes). The complete guide, including normalisation rules and test patterns, is in [docs/ADAPTER_DEVELOPMENT.md](docs/ADAPTER_DEVELOPMENT.md).

---

## Contributing

Contributions are welcome — new adapters especially. Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, style, and PR conventions, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community standards. Run the test suite with `pytest`; integration tests expect a reachable PostgreSQL (e.g. `docker compose up -d postgres`).

## License

Throughline is released under the [MIT License](LICENSE).

## Acknowledgments

- [pgvector](https://github.com/ankane/pgvector) — vector similarity search in PostgreSQL
- [FastAPI](https://github.com/fastapi/fastapi) and [React](https://react.dev) — web UI and API
- [Model Context Protocol](https://modelcontextprotocol.io) — runtime memory access for AI clients
