# Throughline — One Memory, Every AI CLI on Your Laptop

> **Every local AI CLI forgets between sessions. Throughline makes them stop forgetting — without sending your sessions anywhere.**

One PostgreSQL database on your laptop ingests session files from **Claude Code, Codex, Hermes, Continue, Cline, Windsurf, and Vibe (Mistral AI)**, extracts structured memory chunks, and feeds the unified history back to whichever tool you happen to be using next.

---

## Quick Start

### Option A: Docker (Recommended)
```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
docker compose up -d
# Open http://localhost:8501
```

### Option B: Native Installation
```bash
# Clone the repo
git clone https://github.com/mkupermann/throughline.git
cd throughline

# Install dependencies
pip install -r requirements.txt

# Set up PostgreSQL
createdb throughline
psql throughline < sql/schema.sql

# Start Throughline
streamlit run gui/app.py
```

---

## Core Features

### Multi-Source Session Ingestion
Pluggable adapters pull conversations from every local AI tool you use:
- **Claude Code** (`~/.claude/projects/*.jsonl`)
- **OpenAI Codex CLI** (`~/.codex/sessions/<date>/rollout-*.jsonl`)
- **Hermes Agent** (`~/.hermes/sessions/*.json`)
- **Continue.dev** (`~/.continue/sessions/*.json`)
- **Windsurf** (`~/.windsurf/plans/*.md`)
- **Cline** (VS Code per-task directories)
- **Vibe (Mistral AI)** (`~/.vibe/logs/session/session_*/`)

Run `throughline ingest --all` to import from all present adapters.

### Memory Extraction
Sends conversation windows through **Claude CLI** or **Ollama** to extract structured chunks (8 categories: decisions, patterns, contacts, insights, etc.).

### Semantic Search
- **Traditional Embeddings** (OpenAI or Ollama `nomic-embed-text`)
- **HDC Vectors** (10,000-bit hypervectors, inspired by JuiceHDC)

### Knowledge Graph
Entities, relationships, and mentions tracked across sessions with temporal validity (`valid_from` / `valid_until`).

---

## Command Line Interface

```bash
# List all available source adapters
throughline ingest --list-sources

# Ingest from a specific source
throughline ingest --source vibe
throughline ingest --source claude_code
throughline ingest --source hermes

# Ingest from all present adapters
throughline ingest --all

# Generate titles for untitled conversations
throughline generate-titles

# Extract memory chunks from conversations
throughline extract-memory

# Run semantic search
throughline search "find all conversations about authentication"

# Run the self-reflecting memory engine
throughline reflect

# View database status
throughline status

# Launch the Streamlit GUI
throughline gui
```

---

## Architecture

Throughline uses a modular adapter architecture to support multiple AI CLI tools:

```
┌─────────────────────────────────────────────────────────────┐
│                        Throughline                             │
├─────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │ Claude Code  │    │   Hermes     │    │   Vibe       │   │
│  │  Adapter     │    │  Adapter     │    │  Adapter     │   │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘   │
│         │                  │                  │              │
│         └──────────┬────────┴──────────┬─────────┘       │
│                    │                       │                  │
│                    ▼                       ▼                  │
│         ┌─────────────────────────────────────────┐        │
│         │         Normalised Conversations          │        │
│         │  (source-agnostic data structures)        │        │
│         └───────────────────┬────────────────────┘        │
│                             │                               │
│                             ▼                               │
│         ┌─────────────────────────────────────────┐        │
│         │         PostgreSQL + pgvector              │        │
│         │  - conversations table                       │        │
│         │  - messages table                           │        │
│         │  - memory_chunks table                       │        │
│         │  - embeddings (vector search)                │        │
│         └─────────────────────────────────────────┘        │
│                                                                  │
└─────────────────────────────────────────────────────────────┘
```

Each adapter is responsible for:
1. **Discovery** - Finding conversation files in the tool's storage directory
2. **Parsing** - Converting tool-specific formats to normalised conversations
3. **Metadata extraction** - Extracting model, project, timestamps, and other metadata

---

## Adding a New Adapter

To add support for a new AI CLI tool:

1. Create a new file in `throughline/adapters/<name>.py`:

```python
from throughline.adapters.base import Adapter, NormalisedConversation, NormalisedMessage
from pathlib import Path
from typing import Iterable

class MyToolAdapter(Adapter):
    name = "my_tool"
    label = "My Tool"
    home = Path("~/my_tool/sessions").expanduser()
    
    def discover(self) -> Iterable[Path]:
        """Yield candidate conversation files."""
        # Return paths to conversation files/directories
        pass
    
    def parse(self, path: Path) -> NormalisedConversation | None:
        """Parse a single conversation file."""
        # Parse and return a normalised conversation
        pass
```

2. Register the adapter in `throughline/adapters/registry.py`:

```python
_BUILTIN_PATHS: tuple[str, ...] = (
    # ... existing adapters
    "throughline.adapters.my_tool:MyToolAdapter",
)
```

3. Add unit tests in `tests/test_adapter_my_tool.py` following the pattern of existing adapter tests.

---

## Documentation
- [Wiki](https://github.com/mkupermann/throughline/wiki) – Detailed guides and tutorials
- [API Documentation](docs/api.md) – Adapter and database API reference
- [Examples](docs/examples/) – Usage examples

---

## Contributing
Throughline is open-source! Contributions are welcome:
- **Report Issues** – Bug reports and feature requests
- **Pull Requests** – Improve code or documentation
- **Documentation** – Help improve the wiki and guides

---

## License
Throughline is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## Acknowledgments
- **[pgvector](https://github.com/ankane/pgvector)** – Vector search in PostgreSQL
- **[Streamlit](https://github.com/streamlit/streamlit)** – User interface
