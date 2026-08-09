# Throughline — One Memory, Every AI CLI on Your Laptop

> **Every local AI CLI forgets between sessions. Throughline makes them stop forgetting — without sending your sessions anywhere.**

One PostgreSQL database on your laptop ingests session files from **all major AI CLIs** — including **Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, and Vibe (Mistral AI)** — extracts structured memory chunks, and feeds the unified history back to whichever tool you happen to be using next.

Throughline is **vendor-agnostic**: it doesn't matter which AI assistant you use today or tomorrow. Switch between Mistral, Anthropic, OpenAI, or any other provider — your memory stays intact.

---

## Architecture Overview

Throughline's architecture features a **universal adapter system** for all AI CLI tools:

![Throughline Universal Architecture](docs/assets/architecture.svg)

*Universal Adapter Layer → Normalised Conversations → PostgreSQL + pgvector + Knowledge Graph + Semantic Search*

---

## Supported AI CLI Tools

All adapters ingest conversation data from their respective storage formats:

| Tool | Vendor | Storage Location | Format |
|------|--------|------------------|--------|
| **Claude Code** | Anthropic | `~/.claude/projects/<project>/*.jsonl` | Session transcripts |
| **Cursor** | Anysphere | `~/.cursor/sessions/*.jsonl` | Session transcripts |
| **Zed** | Zed Industries | `~/.zed/data/sessions/*.json` | Session transcripts |
| **Vibe** | Mistral AI | `~/.vibe/logs/session/session_*/` | Session transcripts |
| **Codex** | OpenAI | `~/.codex/sessions/<date>/rollout-*.jsonl` | Session transcripts |
| **Hermes** | Community | `~/.hermes/sessions/*.json` | Session transcripts |
| **Continue** | Continue.dev | `~/.continue/sessions/*.json` | Session transcripts |
| **Windsurf** | Codeium/Cognition | `~/.windsurf/plans/*.md` | Plan documents |
| **Cline** | Cline | VS Code per-task directories | Per-task files |

Run `throughline ingest --all` to import from all present adapters.

---

## Data Flow

![Throughline Data Flow](docs/assets/data_flow.svg)

---

## Technical Details

### Adapter Contract
Each adapter follows a simple contract:
- `discover()`: Find conversation files in the tool's storage directory
- `parse()`: Convert tool-specific formats to normalised conversations
- `home`: Default storage directory path

### Sequence Diagram
![Universal Session Ingestion Sequence](docs/assets/sequence_diagram.svg)

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
pip install -e .

# Set up PostgreSQL
createdb throughline
psql throughline < sql/schema.sql

# Start Throughline
streamlit run gui/app.py
```

---

## Command Line Interface

```bash
# List all available source adapters
throughline ingest --list-sources

# Ingest from a specific source
throughline ingest --source vibe
throughline ingest --source claude_code
throughline ingest --source cursor
throughline ingest --source zed
throughline ingest --source hermes
throughline ingest --source codex
throughline ingest --source continue
throughline ingest --source cline
throughline ingest --source windsurf

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

## Adding a New Adapter

To add support for a new AI CLI tool:

1. **Create adapter file** in `throughline/adapters/<name>.py`:

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

2. **Register in `throughline/adapters/registry.py`:**

```python
_BUILTIN_PATHS: tuple[str, ...] = (
    # ... existing adapters
    "throughline.adapters.my_tool:MyToolAdapter",
)
```

3. **Add unit tests** in `tests/test_adapter_my_tool.py`

---

## License
Throughline is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## Acknowledgments
- **[pgvector](https://github.com/ankane/pgvector)** — Vector search in PostgreSQL
- **[Streamlit](https://github.com/streamlit/streamlit)** — User interface
- **[JuiceHDC](https://github.com/mkupermann/JuiceHDC)** — HDC vectors and PostgreSQL integration
- **[Vibrasim](https://github.com/mkupermann/vibrasim)** — Data modeling and simulation logic
