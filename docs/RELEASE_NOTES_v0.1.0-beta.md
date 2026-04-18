# Throughline v0.1.0-beta — Initial Public Release

*The thread that survives every session.*

First public release of Throughline. A local-first, self-reflecting
long-term memory database for Claude Code.

## What it does

Claude Code stores each conversation as a flat JSONL file under
`~/.claude/projects/`. Nothing aggregates, searches, or surfaces that
knowledge across sessions. Throughline is the missing layer: a local
PostgreSQL database that ingests sessions, extracts structured memory,
and gives Claude a skill to query its own past.

## Features

### Core pipeline
- Automatic ingestion of Claude Code JSONL sessions with SHA256
  deduplication
- Memory extraction via the Claude CLI — no separate API key needed
  if you have a Max plan
- Skill scanner for `~/.claude/skills/`
- Prompt scanner for `CLAUDE.md` files in your git repos

### Data layer
- 11 tables under `claude_memory`: conversations, messages, memory
  chunks, skills, prompts, projects, entities, relationships, entity
  mentions, embeddings, memory reflections, ingestion log
- pgvector 0.8 with HNSW indexes for semantic search
- Supports both OpenAI (1536d) and local Ollama (768d) embeddings

### Intelligence
- Temporal knowledge graph: entities and typed relationships with
  `valid_from` / `valid_until` tracking
- Self-reflecting memory: dedup, contradiction detection, stale
  detection, and consolidation via Claude CLI
- Conversation title auto-generation
- Context pre-loader hook that injects relevant memories as
  `.claude/MEMORY_CONTEXT.md` on every new Claude session

### GUI
- 14-page Streamlit app (Dashboard, Calendar, Search, Semantic,
  Conversations, Memory, Memory Health, Skills, Knowledge Graph,
  Projects, Prompts, Scheduler, Ingestion, SQL console)
- GitHub-dark theme, no emojis, responsive card layouts
- Click-through from every list to a full detail view
- Click-to-open in Finder for conversations and skills
- Integrated knowledge-graph visualization (streamlit-agraph with
  forceAtlas2 layout, user-adjustable spacing)

### Automation
- launchd jobs for hourly ingestion, daily memory extraction, daily
  `pg_dump` backup with 30-day retention
- Claude Code skill (`bks-claude-memory/`) for natural-language DB
  access from inside sessions

## Install

### Docker (any platform)

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
docker compose up -d
```

### macOS (full integration)

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
./scripts/install.sh
```

See [INSTALLATION.md](INSTALLATION.md) for the detailed path.

## Known limitations

- **macOS-first**: the launchd jobs, AppleScript hooks for Mail and
  Calendar, and the Finder integration are macOS-only. The core stack
  (Postgres, scripts, GUI) runs on Linux, but scheduling needs manual
  setup.
- **Beta**: tested on a single user with roughly 100 sessions and
  2500 messages. Performance at 10k+ sessions is untested.
- **No MCP server yet**: Claude currently talks to the DB via a
  per-session skill. MCP integration is on the roadmap.
- **Windows untested**: WSL2 should work, native Windows is unverified.

## Tested environments

- macOS 15.4, Python 3.14, PostgreSQL 16.13, pgvector 0.8.0
- Ollama 0.5 with `nomic-embed-text`
- Claude Code CLI 2.1.113

## What's next (roadmap)

- MCP server so Claude can query memory via the MCP protocol
- Linux systemd units as launchd equivalents
- Multi-user schemas (per-user namespace)
- Export adapters for Obsidian and Notion
- Docker Compose profile for Claude CLI integration

## License

MIT.

## Credits

Built by [Michael Kupermann](https://github.com/mkupermann) with
Claude Opus 4.7 as a pair-programming partner.

Inspired by:
- The Anthropic Claude Code team
- The Mem0 and Letta projects
- The `pgvector` maintainers

Feedback, issues, and PRs welcome.
