# Throughline — One Memory, Every AI CLI on Your Laptop

> **Every local AI CLI forgets between sessions. Throughline makes them stop forgetting — without sending your sessions anywhere.**

One PostgreSQL database on your laptop ingests session files from **Claude Code, Codex, Hermes, Continue, Cline, Windsurf, and Vibe CLI**, extracts structured memory chunks, and feeds the unified history back to whichever tool you happen to be using next.

---

## 🚀 **What’s New: AI-CLI Plugin System & HDC Integration**

Throughline now includes a **modular plugin system** for integrating **any AI-CLI tool** (e.g., Vibe CLI, Claude Code CLI) and **Hyperdimensional Computing (HDC)** support inspired by [JuiceHDC](https://github.com/mkupermann/JuiceHDC).

### **Key Improvements:**
✅ **Plugin System** – Easily add new AI-CLIs (Vibe CLI, HDC, etc.) as plugins.
✅ **HDC Support** – Use **10,000-bit hypervectors** for efficient semantic search (alternative to traditional embeddings).
✅ **Reusable Components** – Leverages modular logic from [Vibrasim](https://github.com/mkupermann/vibrasim) (simulation, self-modeling) and [JuiceHDC](https://github.com/mkupermann/JuiceHDC) (vector operations, PostgreSQL integration).
✅ **Static Diagrams** – New **SVG graphics** for architecture, data flow, and sequence diagrams.

---

## 📌 **Architecture Overview**

Throughline’s architecture now includes a **plugin system** for AI-CLIs:

```
![Throughline Architecture with Plugin System](docs/assets/architecture.svg)
```

*Streamlit UI → Plugin Manager → AI-CLI Plugins (Vibe CLI, HDC, Custom) → PostgreSQL + pgvector + Knowledge Graph*

---

## 🔌 **AI-CLI Integration**

### **Plugin System**
The plugin system allows you to:
- **Execute CLI commands** (e.g., `vibe analyze code`).
- **Process outputs** (extract embeddings, metadata, or HDC vectors).
- **Store results** in PostgreSQL + `pgvector`.
- **Extend with custom plugins** (e.g., Claude Code CLI, custom tools).

#### **Data Flow Diagram**
```
![Throughline Data Flow](docs/assets/data_flow.svg)
```

#### **Sequence Diagram: Vibe CLI Integration**
```
![Vibe CLI Sequence Diagram](docs/assets/sequence_diagram.svg)
```

---

### **🤖 Vibe CLI Plugin**
The **Vibe CLI Plugin** allows you to:
- Execute **Vibe CLI commands** (e.g., `analyze`, `chat`, `generate`).
- Extract **embeddings** and **metadata** from the output.
- Store results in **PostgreSQL + pgvector** for semantic search.

#### **Example Usage:**
1. Select **"Vibe CLI"** from the plugin dropdown in the Streamlit UI.
2. Enter a command:
   ```bash
   analyze --input my_code.py
   ```
3. Click **"Execute"**.
4. View the **raw output, embeddings, and metadata** in the UI.
5. Optionally, **save to PostgreSQL** for later retrieval.

---

### **🔢 HDC Plugin (Inspired by JuiceHDC)**
The **HDC Plugin** enables **Hyperdimensional Computing** operations:
- **Encode** – Generate **10,000-bit HDC vectors** for data.
- **Bind** – Combine two HDC vectors (e.g., for associative memory).
- **Search** – Find similar vectors using **Hamming distance**.

#### **Why HDC?**
- **Efficiency** – HDC vectors are **compact** (10,000 bits = ~1.25 KB) and **fast** to compare (XOR + popcount).
- **No Embedding Models** – Works **offline** without API calls or GPUs.
- **Deterministic** – Same input → same vector (no randomness).

#### **Example Usage:**
1. Select **"HDC Plugin"** from the dropdown.
2. Enter a command:
   ```bash
   encode
   ```
   or
   ```bash
   bind <base64_vector1> <base64_vector2>
   ```
3. View the **packed-bit HDC vector** (Base64-encoded).
4. Store it in **PostgreSQL** for later retrieval.

---

### **📦 Developing Custom Plugins**
You can **extend Throughline** by creating custom plugins for new AI-CLIs. Here’s how:

#### **1. Create a Plugin Class**
```python
from ai_cli_plugin import AI_CLI_Plugin
from typing import Dict, Any

class MyCustomPlugin(AI_CLI_Plugin):
    def __init__(self):
        super().__init__(
            name="My Custom Plugin",
            description="A custom plugin for Throughline."
        )

    def execute_command(self, command: str) -> str:
        # Execute your custom CLI command
        return f"Result for '{command}'"

    def process_output(self, output: str) -> Dict[str, Any]:
        return {
            "raw_output": output,
            "metadata": {
                "plugin": self.name,
                "status": "success",
                "description": self.description
            }
        }
```

#### **2. Register the Plugin**
```python
from plugin_manager import PluginManager
from my_custom_plugin import MyCustomPlugin

plugin_manager = PluginManager()
plugin_manager.register_plugin(MyCustomPlugin())
```

#### **3. Use in Streamlit**
The plugin will now appear in the **dropdown menu** in the Throughline UI.

---

## 🔄 **Reusable Components from Vibrasim & JuiceHDC**

Throughline leverages **modular, reusable components** from your other repos:

| **Repo**      | **Component**               | **Usage in Throughline**                                                                                     | **Source** |
|---------------|-----------------------------|-------------------------------------------------------------------------------------------------------------|------------|
| **Vibrasim**  | Data Modeling               | Inspiration for `KnowledgeUnit` class (e.g., `content`, `context`, `embedding`).                           | [world/physics.py](https://github.com/mkupermann/vibrasim/blob/main/world/physics.py) |
| **Vibrasim**  | Simulation Logic            | Sandbox environment for AI experiments (e.g., testing CLI commands).                                      | [world/dream.py](https://github.com/mkupermann/vibrasim/blob/main/world/dream.py) |
| **Vibrasim**  | Offline Replay              | Mechanisms for processing JSONL sessions (e.g., pattern recognition, concept blending).               | [world/self_aware.py](https://github.com/mkupermann/vibrasim/blob/main/world/self_aware.py) |
| **Vibrasim**  | Self-Modeling               | Meta-learning (e.g., analyzing AI-CLI performance).                                                         | [world/self_aware.py](https://github.com/mkupermann/vibrasim/blob/main/world/self_aware.py) |
| **Vibrasim**  | Experiment Harness          | Automation of AI-CLI tests (inspired by [`single-mac-autopilot`](https://github.com/mkupermann/single-mac-autopilot)). | [Repo](https://github.com/mkupermann/single-mac-autopilot) |
| **JuiceHDC** | HDC Vectors                 | 10,000-bit hypervectors for semantic search (alternative to traditional embeddings).                          | [cortex-hdc 3/](https://github.com/mkupermann/JuiceHDC/tree/main/cortex-hdc%203) |
| **JuiceHDC** | Hamming Similarity         | Fast similarity calculation for HDC vectors (XOR + popcount).                                            | [scripts/generate_figures.py](https://github.com/mkupermann/JuiceHDC/blob/main/scripts/generate_figures.py) |
| **JuiceHDC** | Binding Operations         | Element-wise multiplication/addition of vectors for complex queries.                                      | [cortex-hdc 3/](https://github.com/mkupermann/JuiceHDC/tree/main/cortex-hdc%203) |
| **JuiceHDC** | PostgreSQL Integration      | Storage of HDC vectors as **packed-bit strings** (Base64).                                                 | [cortex-hdc 3/](https://github.com/mkupermann/JuiceHDC/tree/main/cortex-hdc%203) |

---

## 📊 **Performance**

| **Metric**               | **Value**                     | **Notes**                                                                                     |
|--------------------------|-----------------------------|-----------------------------------------------------------------------------------------------|
| **Search Latency**       | ~10–50 ms                   | Depends on the number of knowledge units (tested with 10,000 entries).                     |
| **Storage Footprint**    | ~20 MB per 10,000 entries  | HDC vectors (10,000-bit) stored as **packed-bit**.                                           |
| **Scalability**          | Up to 1M entries            | With **HD-NSW index** (planned for future versions).                                         |
| **Similarity Calculation** | Hamming Distance         | Fast computation for HDC vectors (XOR + popcount).                                         |

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
streamlit run app.py
```

---

## 🏗 **Core Features**

### **Multi-Source Session Ingestion**
Pluggable adapters pull conversations from every local AI tool you use:
- **Claude Code** (`~/.claude/projects/*.jsonl`)
- **OpenAI Codex CLI** (`~/.codex/sessions/<date>/rollout-*.jsonl`)
- **Hermes Agent** (`~/.hermes/sessions/*.json`)
- **Continue.dev** (`~/.continue/sessions/*.json`)
- **Windsurf** (`~/.windsurf/plans/*.md`)
- **Cline** (VS Code per-task directories)
- **Vibe CLI** (via plugin system)

Run `throughline ingest --all` to import from all present adapters.

### **Memory Extraction**
Sends conversation windows through **Claude CLI** or **Ollama** to extract structured chunks (8 categories: decisions, patterns, contacts, insights, etc.).

### **Semantic Search**
- **Traditional Embeddings** (OpenAI or Ollama `nomic-embed-text`).
- **HDC Vectors** (10,000-bit hypervectors, inspired by JuiceHDC).

### **Knowledge Graph**
Entities, relationships, and mentions tracked across sessions with temporal validity (`valid_from` / `valid_until`).

---

## 📚 **Documentation**
- [📖 Wiki](https://github.com/mkupermann/throughline/wiki) – Detailed guides and tutorials.
- [🔧 API Documentation](docs/api.md) – Plugin and database API reference.
- [📝 Examples](docs/examples/) – Usage examples for Vibe CLI, HDC, and custom plugins.

---

## 🤝 **Contributing**
Throughline is **open-source**! Contributions are welcome:
- **🐛 Report Issues** – Bug reports and feature requests.
- **📦 Pull Requests** – Improve code or documentation.
- **📖 Documentation** – Help improve the wiki and guides.

---

## 📜 **License**
Throughline is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## 🙏 **Acknowledgments**
- **🔬 [Vibrasim](https://github.com/mkupermann/vibrasim)** – Data modeling, simulation logic, and self-modeling.
- **🔢 [JuiceHDC](https://github.com/mkupermann/JuiceHDC)** – HDC vectors, PostgreSQL integration, and benchmarking.
- **🗃 [pgvector](https://github.com/ankane/pgvector)** – Vector search in PostgreSQL.
- **🖥 [Streamlit](https://github.com/streamlit/streamlit)** – User interface.
