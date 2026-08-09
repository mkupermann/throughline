# Throughline — One Memory, Every AI CLI on Your Laptop

> **Every local AI CLI forgets between sessions. Throughline makes them stop forgetting — without sending your sessions anywhere.**

One PostgreSQL database on your laptop ingests session files from **all major AI CLIs** — including **Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, and Vibe (Mistral AI)** — extracts structured memory chunks, and feeds the unified history back to whichever tool you happen to be using next.

Throughline is **vendor-agnostic**: it doesn't matter which AI assistant you use today or tomorrow. Switch between Mistral, Anthropic, OpenAI, or any other provider — your memory stays intact.

---

## 🚀 **What’s New: Universal Adapter System**

Throughline now features a **comprehensive universal adapter architecture** supporting **9 major AI CLI tools** with **professional business-grade visualizations**.

### **Key Improvements:**
✅ **Universal Architecture** – Support for ALL major AI CLIs (9 adapters: Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, Vibe)
✅ **Professional Graphics** – Enhanced SVG architecture, data flow, and sequence diagrams
✅ **Vendor-Agnostic Design** – Switch between any AI provider without losing memory
✅ **Enterprise-Ready** – Business-grade documentation and visualizations

---

## 📌 **Architecture Overview**

Throughline’s architecture features a **universal adapter system** for all AI CLI tools:

![Throughline Universal Architecture](docs/assets/architecture.svg)

*Universal Adapter Layer → Normalised Conversations → PostgreSQL + pgvector + Knowledge Graph + Semantic Search*

---

## 🔌 **Universal AI CLI Integration**

### **Adapter System**
The universal adapter architecture allows you to:
- **Ingest sessions** from any supported AI CLI tool
- **Normalise conversations** to a common format
- **Store in PostgreSQL** with pgvector for semantic search
- **Extend with custom adapters** for new tools

#### **Architecture Diagram**
![Throughline Universal Architecture](docs/assets/architecture.svg)

#### **Data Flow Diagram**
![Throughline Data Flow](docs/assets/data_flow.svg)

---

## 🏗 **Supported AI CLI Tools (9 Adapters)**

### **Tier 1: Major AI Assistants**
| Tool | Vendor | Storage Location | Status |
|------|--------|------------------|--------|
| **Claude Code** | Anthropic | `~/.claude/projects/*.jsonl` | ✅ Full Support |
| **Cursor** | Anysphere | `~/.cursor/sessions/*.jsonl` | ✅ Full Support |
| **Zed** | Zed Industries | `~/.zed/data/sessions/*.json` | ✅ Full Support |
| **Vibe** | Mistral AI | `~/.vibe/logs/session/session_*/` | ✅ Full Support |

### **Tier 2: Specialized Tools**
| Tool | Vendor | Storage Location | Status |
|------|--------|------------------|--------|
| **Codex** | OpenAI | `~/.codex/sessions/<date>/rollout-*.jsonl` | ✅ Full Support |
| **Hermes** | 11x11 | `~/.hermes/sessions/*.json` | ✅ Full Support |
| **Continue** | Continue.dev | `~/.continue/sessions/*.json` | ✅ Full Support |

### **Tier 3: Development Tools**
| Tool | Vendor | Storage Location | Status |
|------|--------|------------------|--------|
| **Windsurf** | WindSurf | `~/.windsurf/plans/*.md` | ✅ Full Support |
| **Cline** | Cline | VS Code per-task directories | ✅ Full Support |

Run `throughline ingest --all` to import from all present adapters.

---

## 🔧 **Technical Architecture**

### **Sequence Diagram: Universal Session Ingestion**
![Universal Session Ingestion Sequence](docs/assets/sequence_diagram.svg)

Each adapter follows a simple contract:
- `discover()`: Find conversation files in the tool's storage directory
- `parse()`: Convert tool-specific formats to normalised conversations
- `home`: Default storage directory path

---

## 📊 **Performance Characteristics**

| **Metric** | **Value** | **Notes** |
|-----------|-----------|-----------|
| **Search Latency** | ~10–50 ms | Depends on knowledge base size |
| **Storage Footprint** | ~20 MB per 10K entries | HDC vectors stored as packed-bit |
| **Scalability** | Up to 1M entries | With HD-NSW index (planned) |
| **Adapter Coverage** | 9 major AI CLIs | All major vendors supported |
| **Extensibility** | 3 lines of code | Add new adapters easily |

---

## 🚀 **Quick Start**

### **Option A: Docker (Recommended)**
```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
docker compose up -d
# Open http://localhost:8501
```

### **Option B: Native Installation**
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

## 🎯 **Core Features**

### **Multi-Source Session Ingestion**
Pluggable adapters pull conversations from every local AI tool you use:
- **Claude Code** (`~/.claude/projects/*.jsonl`) — Anthropic
- **Cursor** (`~/.cursor/sessions/*.jsonl`) — Anysphere
- **Zed** (`~/.zed/data/sessions/*.json`) — Zed Industries
- **OpenAI Codex CLI** (`~/.codex/sessions/<date>/rollout-*.jsonl`) — OpenAI
- **Hermes Agent** (`~/.hermes/sessions/*.json`) — 11x11
- **Continue.dev** (`~/.continue/sessions/*.json`) — Continue
- **Windsurf** (`~/.windsurf/plans/*.md`) — WindSurf
- **Cline** (VS Code per-task directories) — Cline
- **Vibe (Mistral AI)** (`~/.vibe/logs/session/session_*/`) — Mistral AI

### **Memory Extraction**
Sends conversation windows through **Claude CLI** or **Ollama** to extract structured chunks (8 categories: decisions, patterns, contacts, insights, etc.).

### **Semantic Search**
- **Traditional Embeddings** (OpenAI or Ollama `nomic-embed-text`)
- **HDC Vectors** (10,000-bit hypervectors, inspired by JuiceHDC)

### **Knowledge Graph**
Entities, relationships, and mentions tracked across sessions with temporal validity (`valid_from` / `valid_until`).

---

## 📝 **Command Line Interface**

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

## 📦 **Adding a New Adapter**

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

## 🏢 **Enterprise Features**

### **Vendor Agnostic**
- Works with ANY AI CLI tool
- No vendor lock-in
- Maintain memory across provider switches

### **Professional Graphics**
- Business-grade SVG diagrams
- Clear architecture visualization
- Professional documentation

### **Extensible Architecture**
- Simple adapter interface
- Easy to add new tools
- Modular design

---

## 📚 **Documentation**
- [Wiki](https://github.com/mkupermann/throughline/wiki) – Detailed guides and tutorials
- [API Documentation](docs/api.md) – Adapter and database API reference
- [Examples](docs/examples/) – Usage examples

---

## 🤝 **Contributing**
Throughline is open-source! Contributions are welcome:
- **Report Issues** – Bug reports and feature requests
- **Pull Requests** – Improve code or documentation
- **Documentation** – Help improve the wiki and guides

---

## 📜 **License**
Throughline is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## 🙏 **Acknowledgments**
- **[pgvector](https://github.com/ankane/pgvector)** – Vector search in PostgreSQL
- **[Streamlit](https://github.com/streamlit/streamlit)** – User interface
- **[JuiceHDC](https://github.com/mkupermann/JuiceHDC)** – HDC vectors and PostgreSQL integration
- **[Vibrasim](https://github.com/mkupermann/vibrasim)** – Data modeling and simulation logic
