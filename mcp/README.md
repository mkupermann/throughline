# Throughline MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes
the Throughline memory database directly to any MCP-compatible client — Claude
Code, Claude Desktop, Cursor, Continue, Zed, and others.

Once registered, the LLM can query Throughline natively (no skill round-trip, no
shelling out, no CLAUDE.md injection):

```
User:   what did we decide about pgvector last month?
Claude: [calls throughline.list_decisions(project="...", days=45)]
        [returns 3 decisions with content, confidence, project, date]
```

---

## What it does

The server connects to the local `claude_memory` PostgreSQL database and
exposes **10 tools** over stdio:

| Tool | Purpose |
|---|---|
| `search_memory(query, limit=10)` | Full-text (trigram) search over `memory_chunks` and `messages`. |
| `search_semantic(query, limit=10)` | pgvector cosine search. Auto-falls back to ILIKE if no embedding backend is available. |
| `get_project_context(project_name)` | All memory chunks for a project, grouped by category. |
| `get_recent_conversations(project=None, limit=10)` | Newest sessions with titles/summaries. |
| `get_conversation(conversation_id)` | Full transcript of a single session. |
| `list_decisions(project=None, days=30)` | Chronological `decision` chunks. |
| `find_contact(name)` | Contact lookup across `memory_chunks` and the `entities` graph. |
| `list_entities(entity_type=None, min_mentions=3)` | Top knowledge-graph entities. |
| `get_entity_relations(entity_name)` | Incoming + outgoing relationships of an entity. |
| `add_memory(content, category, tags=[], project=None, confidence=0.8)` | Persist a new memory chunk from inside a session. |

All tools return JSON-safe dicts with a top-level `ok: bool` plus a sensible
error message when something goes wrong (DB down, bad argument, etc.). No
tool ever crashes the server.

---

## Installation

### 1. Install the Python dependencies

From the repository root:

```bash
pip install -r mcp/requirements.txt
```

This pulls in:

- `mcp >= 1.0` — the official [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- `psycopg2-binary >= 2.9.9` — PostgreSQL driver

If you already have `psycopg2` from the main Throughline install, only the
`mcp` package is actually added.

### 2. Make sure Throughline's database is reachable

The server reads the same env vars as the rest of Throughline:

```
PGHOST        (default: localhost)
PGPORT        (default: 5432)
PGDATABASE    (default: claude_memory)
PGUSER        (default: $USER)
PGPASSWORD    (optional; not needed for local trust auth)
```

A dry-run that just verifies connectivity:

```bash
python3 -c "import os, psycopg2; \
  psycopg2.connect(dbname='claude_memory', user=os.environ.get('PGUSER') or os.environ['USER'], host='localhost', port=5432).close(); \
  print('db ok')"
```

### 3. Register the server with Claude Code

Claude Code ships a `claude mcp` CLI that adds a server to your
`~/.claude.json` (user scope) or `.mcp.json` (project scope).

Pick whichever syntax your Claude Code version supports — both are shown
below; the first one is current as of the Claude Code CLI docs, the second
is the older explicit form:

```bash
# Preferred (Claude Code >= 1.x):
claude mcp add throughline \
  --command python3 \
  --args /absolute/path/to/throughline/mcp/server.py

# Or, if your CLI uses the shorthand positional form:
claude mcp add throughline python3 /absolute/path/to/throughline/mcp/server.py

# Or, for project scope (adds .mcp.json in the current repo):
claude mcp add --scope project throughline python3 /absolute/path/to/throughline/mcp/server.py
```

Pass any required env vars through with `--env`:

```bash
claude mcp add throughline python3 /abs/path/to/throughline/mcp/server.py \
  --env PGHOST=localhost \
  --env PGDATABASE=claude_memory \
  --env PGUSER=$USER
```

Verify:

```bash
claude mcp list
# → throughline  (stdio)  python3 /abs/path/to/.../mcp/server.py
```

Restart Claude Code. The tools appear under the `throughline` namespace
(e.g. `throughline.search_memory`) and the model can call them without any
explicit permission per call once you allow the server once.

### 4. (Optional) Use it from Claude Desktop or another client

Any MCP client that supports stdio servers works. The equivalent entry for
`~/Library/Application Support/Claude/claude_desktop_config.json` is:

```json
{
  "mcpServers": {
    "throughline": {
      "command": "python3",
      "args": ["/absolute/path/to/throughline/mcp/server.py"],
      "env": {
        "PGDATABASE": "claude_memory",
        "PGUSER": "your-os-user",
        "PGHOST": "localhost",
        "PGPORT": "5432"
      }
    }
  }
}
```

---

## Example prompts

Once the server is registered, these prompts will route through MCP tools
without any extra ceremony:

1. **"What do I know about pgvector?"**
   → `search_memory(query="pgvector")` + `search_semantic(query="pgvector")`

2. **"What did we decide about the deployment architecture last month?"**
   → `list_decisions(project="<current-project>", days=45)`

3. **"Pull up everything for project `throughline`."**
   → `get_project_context(project_name="throughline")`

4. **"Who did I talk to about Stepstone Peppol?"**
   → `find_contact(name="Stepstone")` + `list_entities(entity_type="person")`

5. **"Remember: we decided to always use HNSW instead of IVFFlat for embeddings on this project."**
   → `add_memory(content="Always use HNSW instead of IVFFlat for pgvector indexes on this project.", category="decision", project="throughline", tags=["pgvector", "index"])`

6. **"Show me my last 5 Claude Code sessions on throughline and summarize them."**
   → `get_recent_conversations(project="throughline", limit=5)`

---

## Troubleshooting

### `Cannot reach the Throughline database at localhost:5432/claude_memory`

- Is Postgres running? `brew services list` (macOS) or `systemctl status postgresql` (Linux).
- Can you connect manually? `psql -d claude_memory -c 'SELECT 1'`.
- If you run Postgres in Docker (`docker compose up -d`), make sure port 5432
  is published to the host, then set `PGHOST=localhost` for the MCP server.
- For a containerised server talking to a containerised DB, set
  `PGHOST=postgres` (the service name) in the env block.

### `ModuleNotFoundError: No module named 'mcp'`

You installed into a different Python. Run the server with the same Python
you installed `mcp` into:

```bash
which python3
python3 -m pip show mcp
```

Then pass the absolute interpreter path to `claude mcp add`
(e.g. `/opt/homebrew/bin/python3` or a venv's `python`).

### `fallback: true` in every `search_semantic` response

That means no embedding backend is currently available — the server is
silently using ILIKE search instead of vector search. To enable real
semantic search:

```bash
# Option A — OpenAI
export OPENAI_API_KEY=sk-...
python3 scripts/generate_embeddings.py --backend openai

# Option B — local Ollama (no API key)
ollama pull nomic-embed-text
python3 scripts/generate_embeddings.py --backend ollama
```

Once `embeddings` rows exist, the MCP tool picks the backend up
automatically (via `scripts/generate_embeddings.pick_backend`).

### The server starts but Claude Code never calls a tool

- Check stderr logs: Claude Code pipes server stderr to
  `~/Library/Logs/Claude/mcp-throughline.log` (macOS). Look for
  `Throughline MCP server starting` and `db connection OK`.
- Confirm registration: `claude mcp list`. If it's missing, re-run
  `claude mcp add ...` and restart the client.
- Make sure the absolute path to `server.py` is correct — relative paths
  fail once the client spawns the server from a different cwd.

### I want to see what the server is doing

Bump the log level:

```bash
claude mcp add throughline python3 /abs/path/to/mcp/server.py \
  --env THROUGHLINE_MCP_LOGLEVEL=DEBUG
```

Every tool call and DB query then gets logged to stderr, which Claude Code
captures in its MCP log file.

---

## Development

Run the server standalone — it will block on stdin waiting for JSON-RPC
frames, which is the expected behaviour:

```bash
python3 mcp/server.py
# (Ctrl-C to exit)
```

For an interactive smoke test, use the SDK's `mcp` dev tool:

```bash
pip install 'mcp[cli]'
mcp dev mcp/server.py
```

That opens a browser-based inspector where you can invoke each tool with
arbitrary arguments and see the JSON it returns.
