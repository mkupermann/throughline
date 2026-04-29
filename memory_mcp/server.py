"""MCP server exposing claude-memory-db as an agent memory layer.

Six tools:
  memory.search          — vector search over memory chunks + messages
  memory.recall_entity   — entity neighborhood from the knowledge graph
  memory.write           — append a memory chunk
  memory.supersede       — mark old chunk as superseded by a new one
  memory.forget          — cascade-delete chunks (calls scripts/forget.py)
  memory.list_projects   — distinct project_names in memory_chunks

Stdio transport (FastMCP default), suitable for Claude Code's MCP config.
Run:  python -m memory_mcp.server
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2.extras

# Reuse existing query/forget helpers from gui/ and scripts/.
_ROOT = Path(__file__).resolve().parent.parent
for sub in ("scripts", "gui"):
    p = str(_ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import semantic_helper  # type: ignore  # noqa: E402
from forget import forget_chunks  # type: ignore  # noqa: E402
from graph_query import resolve_entity  # type: ignore  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from .db import connect  # noqa: E402

logger = logging.getLogger("memory-mcp")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s memory-mcp %(message)s",
    stream=sys.stderr,
)

ALLOWED_CATEGORIES = {
    "decision", "pattern", "insight", "preference",
    "contact", "error_solution", "project_context", "workflow",
}

mcp = FastMCP("claude-memory")


# ── Helpers ───────────────────────────────────────────────────────────────────
def default_project() -> str | None:
    pd = os.environ.get("CLAUDE_PROJECT_DIR")
    if not pd:
        return None
    return os.path.basename(pd.rstrip("/")) or None


def _log(tool: str, **kw) -> None:
    parts = " ".join(f"{k}={v!r}" for k, v in kw.items())
    logger.info(f"tool={tool} {parts}")


def _trim_dict(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("embedding_")}


# ── Tools ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def search(
    query: str,
    scope: list[str] | None = None,
    project: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Vector search across memory chunks and conversation messages.

    Args:
        query: Natural-language query.
        scope: Subset of ["memory","messages"]; defaults to both.
        project: Restrict to project_name. Defaults to CLAUDE_PROJECT_DIR
                 basename if set; pass an empty string "" to search all projects.
        limit:  Max rows (default 20).
    """
    if project is None:
        project = default_project()
    elif project == "":
        project = None
    scope_set = set(scope or ["memory", "messages"])

    conn = connect()
    try:
        rows = semantic_helper.semantic_search(conn, query, limit=limit, project=project)
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        st = r.get("source_type")
        if st == "memory_chunk" and "memory" not in scope_set:
            continue
        if st == "message" and "messages" not in scope_set:
            continue
        out.append(_trim_dict(r))

    _log("memory.search", project=project, q=query[:60], rows=len(out))
    return out


@mcp.tool()
def recall_entity(
    name: str,
    hops: int = 1,
    project: str | None = None,
    relation_types: list[str] | None = None,
) -> dict:
    """Walk the knowledge graph from an entity name.

    Args:
        name: Entity name (canonicalised lookup, falls back to ILIKE).
        hops: BFS depth from the seed entity (1–3).
        project: Restrict the seed lookup to this project_name. Defaults to
                 CLAUDE_PROJECT_DIR basename if set; "" to search all.
        relation_types: Optional whitelist of relation_type values
                        (e.g. ["depends_on","decided"]).
    """
    if project is None:
        project = default_project()
    elif project == "":
        project = None
    hops = max(1, min(int(hops), 3))

    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            seeds = resolve_entity(cur, name)
            if not seeds:
                _log("memory.recall_entity", name=name, found=0)
                return {"entity": None, "neighbors": [], "mentions": []}
            if project:
                scoped = [s for s in seeds if s["project_name"] == project]
                if scoped:
                    seeds = scoped

            seed = seeds[0]
            seed_id = seed["id"]

            visited = {seed_id}
            frontier = {seed_id}
            edges: list[dict] = []
            for hop in range(1, hops + 1):
                if not frontier:
                    break
                rt_clause = "AND r.relation_type = ANY(%s)" if relation_types else ""
                params: list = [list(frontier), list(frontier)]
                if relation_types:
                    params.append(list(relation_types))
                cur.execute(
                    f"""
                    SELECT r.from_entity, r.to_entity, r.relation_type, r.confidence
                    FROM relationships r
                    WHERE (r.from_entity = ANY(%s) OR r.to_entity = ANY(%s))
                    {rt_clause}
                    """,
                    params,
                )
                new_frontier: set[int] = set()
                for row in cur.fetchall():
                    fe, te, rel, conf = row["from_entity"], row["to_entity"], row["relation_type"], row["confidence"]
                    if fe in frontier and te not in visited:
                        new_frontier.add(te)
                        edges.append({"id": te, "from": fe, "relation_type": rel, "direction": "outgoing", "hops": hop, "confidence": float(conf or 0)})
                    elif te in frontier and fe not in visited:
                        new_frontier.add(fe)
                        edges.append({"id": fe, "from": te, "relation_type": rel, "direction": "incoming", "hops": hop, "confidence": float(conf or 0)})
                visited |= new_frontier
                frontier = new_frontier

            entity_meta: dict[int, dict] = {}
            if visited:
                cur.execute(
                    "SELECT id, name, entity_type, project_name, mention_count FROM entities WHERE id = ANY(%s)",
                    (list(visited),),
                )
                for r in cur.fetchall():
                    entity_meta[r["id"]] = {
                        "id": r["id"],
                        "name": r["name"],
                        "entity_type": r["entity_type"],
                        "project_name": r["project_name"],
                        "mention_count": r["mention_count"],
                    }

            cur.execute(
                """
                SELECT em.source_type, em.source_id, em.context_snippet, em.created_at
                FROM entity_mentions em
                WHERE em.entity_id = %s
                ORDER BY em.created_at DESC
                LIMIT 20
                """,
                (seed_id,),
            )
            mentions = [
                {
                    "source_type": r["source_type"],
                    "source_id": r["source_id"],
                    "context_snippet": r["context_snippet"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in cur.fetchall()
            ]

            neighbors = []
            for e in edges:
                meta = entity_meta.get(e["id"])
                if meta:
                    neighbors.append({
                        "entity": meta,
                        "relation_type": e["relation_type"],
                        "direction": e["direction"],
                        "hops": e["hops"],
                        "confidence": e["confidence"],
                    })

            _log("memory.recall_entity", name=name, hops=hops, project=project, neighbors=len(neighbors))
            return {
                "entity": entity_meta.get(seed_id),
                "neighbors": neighbors,
                "mentions": mentions,
            }
    finally:
        conn.close()


@mcp.tool()
def write(
    content: str,
    category: str,
    project: str | None = None,
    confidence: float = 0.8,
    tags: list[str] | None = None,
) -> dict:
    """Append a new memory chunk.

    Args:
        content: The memory text. Required.
        category: One of: decision, pattern, insight, preference, contact,
                  error_solution, project_context, workflow.
        project: Project name. Defaults to CLAUDE_PROJECT_DIR basename.
        confidence: 0.0–1.0 (default 0.8).
        tags: Free-form list of tags.
    """
    if not content or not content.strip():
        raise ValueError("content is required")
    if category not in ALLOWED_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(ALLOWED_CATEGORIES)}")
    if project is None:
        project = default_project()
    elif project == "":
        project = None
    confidence = max(0.0, min(float(confidence), 1.0))
    tags = list(tags or [])

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_chunks
                    (source_type, source_id, content, category, tags, confidence, project_name)
                VALUES ('mcp_write', NULL, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (content.strip(), category, tags, confidence, project),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    _log("memory.write", id=new_id, category=category, project=project)
    return {"id": int(new_id)}


@mcp.tool()
def supersede(old_id: int, new_id: int, reason: str) -> dict:
    """Mark a chunk as superseded by another, with audit row."""
    if old_id == new_id:
        raise ValueError("old_id and new_id must differ")
    if not reason or not reason.strip():
        raise ValueError("reason is required")

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM memory_chunks WHERE id = %s", (int(new_id),))
            if cur.fetchone() is None:
                raise ValueError(f"new_id {new_id} not found")
            cur.execute(
                """
                UPDATE memory_chunks
                SET status = 'superseded',
                    superseded_by = %s,
                    superseded_at = %s
                WHERE id = %s
                """,
                (int(new_id), datetime.now(timezone.utc), int(old_id)),
            )
            updated = cur.rowcount
            cur.execute(
                """
                INSERT INTO memory_reflections
                    (reflection_type, affected_chunks, action_taken, reasoning, confidence)
                VALUES ('mcp_supersede', %s, 'superseded', %s, 1.0)
                RETURNING id
                """,
                ([int(old_id), int(new_id)], reason.strip()[:4000]),
            )
            reflection_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    _log("memory.supersede", old=old_id, new=new_id, updated=updated)
    return {"ok": updated > 0, "reflection_id": int(reflection_id)}


@mcp.tool()
def forget(ids: list[int], reason: str) -> dict:
    """Cascade-delete memory chunks: removes the chunk and its embeddings,
    and writes an audit row to memory_reflections."""
    if not ids:
        raise ValueError("ids is required")
    if not reason or not reason.strip():
        raise ValueError("reason is required")

    conn = connect()
    try:
        res = forget_chunks(conn, ids, reason=reason.strip())
    finally:
        conn.close()

    _log("memory.forget", ids=ids, **res)
    return {
        "deleted": {"chunks": res["chunks"], "embeddings": res["embeddings"]},
        "reflection_id": res["reflection_id"],
    }


@mcp.tool()
def list_projects() -> list[str]:
    """Return distinct project_name values across memory_chunks."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT project_name FROM memory_chunks "
                "WHERE project_name IS NOT NULL ORDER BY project_name"
            )
            projects = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    _log("memory.list_projects", n=len(projects))
    return projects


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
