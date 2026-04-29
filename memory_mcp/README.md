# memory_mcp — MCP server for claude-memory-db

Exposes the same query and write surface that `gui/app.py` uses, but over the
Model Context Protocol so Claude Code (or any MCP client) can read and write
its own long-term memory across sessions.

## Tools

| Tool | Purpose |
|---|---|
| `memory.search(query, scope, project, limit)` | Vector search over memory chunks and conversation messages. |
| `memory.recall_entity(name, hops, project, relation_types)` | BFS the knowledge graph from an entity, return neighbors + recent mentions. |
| `memory.write(content, category, project, confidence, tags)` | Append a new memory chunk (`source_type='mcp_write'`). |
| `memory.supersede(old_id, new_id, reason)` | Mark a chunk as superseded; logs `memory_reflections.reflection_type='mcp_supersede'`. |
| `memory.forget(ids, reason)` | Cascade-delete chunks + their embeddings; logs `memory_reflections.reflection_type='forget'`. |
| `memory.list_projects()` | Distinct `project_name` values in `memory_chunks`. |
| `memory.recent_reflections(limit, types)` | Recent rows from the `memory_reflections` audit log — what the reflection engine and the preload hook have been doing. |
| `memory.preload_summary()` | The most recent SessionStart preload row: which chunks the hook injected at the start of this session, and when. |

Every tool with a `project` parameter defaults to the basename of
`$CLAUDE_PROJECT_DIR` if it is set. Pass `project=""` to opt out and search
across projects.

### Strict project isolation

Set `THROUGHLINE_PROJECT_SCOPE_STRICT=1` in the server env to refuse the
`project=""` opt-out across the board. Calls that try to widen scope raise
`ValueError("THROUGHLINE_PROJECT_SCOPE_STRICT is enabled — pass an explicit
project name; cross-project search is disabled by policy.")`. Use this in
multi-tenant or multi-client setups where data isolation is a hard
requirement, not a per-call convention.

## Run

```bash
pip install 'mcp[cli]>=1.2' psycopg2-binary
python -m memory_mcp.server
```

The DB connection honours libpq env vars: `PGHOST`, `PGPORT`, `PGDATABASE`,
`PGUSER`, `PGPASSWORD`. Defaults are `localhost:5432 / claude_memory /
$PGUSER or mkupermann`.

## Wire to Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "claude-memory": {
      "command": "python",
      "args": ["-m", "memory_mcp.server"],
      "cwd": "/absolute/path/to/claude-memory-db",
      "env": {
        "PGHOST": "localhost",
        "PGDATABASE": "claude_memory",
        "PGUSER": "mkupermann"
      }
    }
  }
}
```

Restart Claude Code, then run `/mcp` — the six `claude-memory.*` tools should
list. Try `claude-memory.list_projects` first as a smoke test.

## Verify

```bash
python -c "from memory_mcp.server import mcp; print(sorted(t.name for t in mcp._tool_manager.list_tools()))"
```

Expected output:
```
['forget', 'list_projects', 'recall_entity', 'search', 'supersede', 'write']
```
