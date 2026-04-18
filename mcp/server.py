#!/usr/bin/env python3
"""
Throughline MCP Server.

Exposes the Throughline memory database (PostgreSQL `claude_memory`) to any
MCP-compatible client (Claude Code, Claude Desktop, Cursor, etc.) via stdio
transport. Tools let the client search memory, pull project context, walk the
knowledge graph, and write new memory chunks — without round-tripping through
a user-invoked skill.

Transport: stdio (launched by the client as a subprocess).

Environment variables:
  PGHOST        default: localhost
  PGPORT        default: 5432
  PGDATABASE    default: claude_memory
  PGUSER        default: $USER (or "postgres")
  PGPASSWORD    optional; trust auth works out of the box for local installs.

Run manually:
  python3 mcp/server.py
(it will block on stdin waiting for JSON-RPC frames — that's normal.)
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import re
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

try:
    import psycopg2
    import psycopg2.extras
except ImportError as exc:  # pragma: no cover — surface the missing dep clearly
    sys.stderr.write(
        "Throughline MCP: psycopg2 is not installed.\n"
        "Install with:  pip install -r mcp/requirements.txt\n"
    )
    raise

# The official Python SDK ships a high-level FastMCP API. We import it lazily
# so that the module still imports in environments without the SDK (e.g. CI
# lint) — the server simply cannot start without it.
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore


# ---------------------------------------------------------------------------
# Logging — MCP uses stdio for protocol frames, so ALL logs must go to stderr.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("THROUGHLINE_MCP_LOGLEVEL", "INFO"),
    format="%(asctime)s [throughline-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("throughline.mcp")


# ---------------------------------------------------------------------------
# DB config — identical conventions to scripts/context_preload.py.
# ---------------------------------------------------------------------------
def _db_config() -> dict[str, Any]:
    user = os.environ.get("PGUSER") or os.environ.get("USER") or getpass.getuser() or "postgres"
    cfg: dict[str, Any] = {
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": user,
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", "5")),
    }
    pw = os.environ.get("PGPASSWORD")
    if pw:
        cfg["password"] = pw
    return cfg


DB_CONFIG = _db_config()


# ---------------------------------------------------------------------------
# DB helpers.
# ---------------------------------------------------------------------------
class DBError(Exception):
    """Raised when the memory database is unreachable or a query fails."""


@contextmanager
def db_cursor():
    """
    Context manager yielding a RealDictCursor. Commits on exit if the block
    performed writes, rolls back on exception.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as exc:
        raise DBError(
            f"Cannot reach the Throughline database at "
            f"{DB_CONFIG.get('host')}:{DB_CONFIG.get('port')}/{DB_CONFIG.get('dbname')}: {exc}"
        ) from exc

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def _jsonable(value: Any) -> Any:
    """Make psycopg2 / datetime / Decimal objects JSON-safe."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    # Decimal, UUID, etc.
    try:
        return float(value)  # Decimal
    except (TypeError, ValueError):
        return str(value)


def _rows(cur) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in cur.fetchall()]


def _canonicalize(name: str) -> str:
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    no_accent = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accent).strip().lower()


def _error(msg: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": msg}
    out.update(extra)
    return out


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    out.update(payload)
    return out


# ---------------------------------------------------------------------------
# Tool implementations. Each returns a JSON-safe dict.
# ---------------------------------------------------------------------------
def tool_search_memory(query: str, limit: int = 10) -> dict[str, Any]:
    """Full-text search over memory_chunks and message bodies (trigram + ILIKE).

    Args:
        query: Free-form search string. Case-insensitive, matched against
               content, tags, and project names.
        limit: Maximum number of rows to return per source type. Default 10.

    Returns a dict with keys:
        ok             : bool
        query          : the input query
        total_results  : total rows returned across both sources
        memory_chunks  : list of {id, content, category, confidence, project,
                         tags, created_at}
        messages       : list of {id, conversation_id, role, content, project,
                         created_at}
    """
    if not query or not query.strip():
        return _error("query must be a non-empty string")
    limit = max(1, min(int(limit or 10), 100))
    like = f"%{query.strip()}%"

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       content,
                       category::text AS category,
                       confidence::float AS confidence,
                       project_name AS project,
                       tags,
                       created_at
                FROM memory_chunks
                WHERE status = 'active'
                  AND (content ILIKE %s
                       OR %s = ANY(tags)
                       OR project_name ILIKE %s)
                ORDER BY confidence DESC NULLS LAST, created_at DESC
                LIMIT %s
                """,
                (like, query.strip(), like, limit),
            )
            chunks = _rows(cur)

            cur.execute(
                """
                SELECT m.id,
                       m.conversation_id,
                       m.role::text AS role,
                       m.content,
                       c.project_name AS project,
                       m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.content ILIKE %s
                ORDER BY m.created_at DESC
                LIMIT %s
                """,
                (like, limit),
            )
            msgs = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok(
        {
            "query": query,
            "total_results": len(chunks) + len(msgs),
            "memory_chunks": chunks,
            "messages": msgs,
        }
    )


def tool_search_semantic(query: str, limit: int = 10) -> dict[str, Any]:
    """Semantic / vector search over memory_chunks and messages via pgvector.

    Uses whatever embedding model is already present in the `embeddings` table
    (OpenAI text-embedding-3-small or Ollama nomic-embed-text). We embed the
    query by calling the same backend Throughline uses for ingestion
    (scripts/generate_embeddings.py). If no embedding backend is available,
    the tool falls back to the same ILIKE search as `search_memory` and sets
    `fallback=True` in the response.

    Args:
        query: Free-form search query.
        limit: Max rows (1-100). Default 10.

    Returns:
        ok, query, backend, fallback, memory_chunks, messages.
    """
    if not query or not query.strip():
        return _error("query must be a non-empty string")
    limit = max(1, min(int(limit or 10), 100))

    # Try to reuse the project's existing embedding helpers.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    scripts_dir = os.path.join(repo_root, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    backend = None
    backend_err: str | None = None
    try:
        import generate_embeddings as ge  # type: ignore

        backend = ge.pick_backend("auto")
    except Exception as exc:  # ModuleNotFoundError, missing API key, Ollama down, ...
        backend_err = f"{type(exc).__name__}: {exc}"
        log.info("semantic backend unavailable (%s) — falling back to ILIKE", backend_err)

    if backend is None:
        res = tool_search_memory(query, limit=limit)
        res["fallback"] = True
        res["backend"] = None
        res["backend_error"] = backend_err
        return res

    try:
        vec = backend.embed([query[: backend.max_chars]])[0]
        v_lit = "[" + ",".join(f"{v:.7f}" for v in vec) + "]"
        col = backend.column
    except Exception as exc:
        # Embedding failed at runtime. Fall back rather than crash.
        res = tool_search_memory(query, limit=limit)
        res["fallback"] = True
        res["backend"] = backend.name
        res["backend_error"] = f"embed failed: {exc}"
        return res

    try:
        with db_cursor() as cur:
            sql = f"""
                WITH mc AS (
                    SELECT 'memory_chunk'::text AS source_type,
                           mc.id AS source_id,
                           mc.content,
                           mc.category::text AS meta,
                           mc.project_name,
                           mc.confidence::float AS confidence,
                           e.{col} <=> %s::vector AS distance
                    FROM embeddings e
                    JOIN memory_chunks mc ON mc.id = e.source_id
                    WHERE e.source_type = 'memory_chunk'
                      AND e.model = %s
                      AND e.{col} IS NOT NULL
                      AND mc.status = 'active'
                ),
                ms AS (
                    SELECT 'message'::text AS source_type,
                           m.id AS source_id,
                           m.content,
                           m.role::text AS meta,
                           c.project_name,
                           NULL::float AS confidence,
                           e.{col} <=> %s::vector AS distance
                    FROM embeddings e
                    JOIN messages m ON m.id = e.source_id
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE e.source_type = 'message'
                      AND e.model = %s
                      AND e.{col} IS NOT NULL
                )
                SELECT * FROM (
                    SELECT * FROM mc UNION ALL SELECT * FROM ms
                ) x
                ORDER BY distance ASC
                LIMIT %s
            """
            cur.execute(sql, (v_lit, backend.model, v_lit, backend.model, limit))
            rows = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    chunks, msgs = [], []
    for r in rows:
        dist = r.get("distance")
        item = {
            "id": r["source_id"],
            "content": r["content"],
            "project": r.get("project_name"),
            "distance": dist,
            "similarity": (1.0 - float(dist)) if dist is not None else None,
        }
        if r["source_type"] == "memory_chunk":
            item["category"] = r.get("meta")
            item["confidence"] = r.get("confidence")
            chunks.append(item)
        else:
            item["role"] = r.get("meta")
            msgs.append(item)

    return _ok(
        {
            "query": query,
            "backend": backend.name,
            "model": backend.model,
            "fallback": False,
            "memory_chunks": chunks,
            "messages": msgs,
        }
    )


def tool_get_project_context(project_name: str) -> dict[str, Any]:
    """All memory grouped by category for a given project.

    Returns decisions, preferences, patterns, contacts, project_context,
    error_solutions, insights, and workflows. Match is ILIKE on
    `memory_chunks.project_name` (so "myapp" also matches "my-app").

    Args:
        project_name: The project name (or a fragment of it).

    Returns:
        ok, project (echoed), categories: dict[str, list[chunk]].
    """
    if not project_name or not project_name.strip():
        return _error("project_name must be a non-empty string")
    like = f"%{project_name.strip()}%"

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       content,
                       category::text AS category,
                       tags,
                       confidence::float AS confidence,
                       project_name AS project,
                       created_at
                FROM memory_chunks
                WHERE status = 'active'
                  AND project_name ILIKE %s
                ORDER BY category::text, confidence DESC NULLS LAST, created_at DESC
                """,
                (like,),
            )
            chunks = _rows(cur)

            # Also pull the projects table row if it exists for richer metadata.
            cur.execute(
                """
                SELECT name, description, contacts, decisions, status::text AS status,
                       created_at, updated_at
                FROM projects
                WHERE name ILIKE %s
                ORDER BY updated_at DESC
                LIMIT 5
                """,
                (like,),
            )
            project_rows = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        grouped.setdefault(c["category"], []).append(c)

    return _ok(
        {
            "project": project_name,
            "total_chunks": len(chunks),
            "categories": grouped,
            "projects_table": project_rows,
        }
    )


def tool_get_recent_conversations(project: str | None = None, limit: int = 10) -> dict[str, Any]:
    """List the most recent Claude Code sessions.

    Args:
        project: Optional project name fragment to filter by (ILIKE).
        limit: Max sessions (1-100). Default 10.

    Returns a list of sessions with id, session_id, project_name, model,
    started_at, ended_at, message_count, summary.
    """
    limit = max(1, min(int(limit or 10), 100))
    params: list[Any] = []
    where = ""
    if project:
        where = "WHERE project_name ILIKE %s"
        params.append(f"%{project.strip()}%")
    params.append(limit)

    try:
        with db_cursor() as cur:
            cur.execute(
                f"""
                SELECT id,
                       session_id::text AS session_id,
                       project_name,
                       model,
                       started_at,
                       ended_at,
                       message_count,
                       token_count_in,
                       token_count_out,
                       summary,
                       tags
                FROM conversations
                {where}
                ORDER BY started_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok({"count": len(rows), "conversations": rows})


def tool_get_conversation(conversation_id: int) -> dict[str, Any]:
    """Fetch a full conversation by its numeric id, including all messages.

    Args:
        conversation_id: The `conversations.id` primary key.

    Returns:
        ok, conversation: {...session metadata...},
        messages: ordered list of {id, role, content, tool_name, created_at}.
    """
    try:
        conv_id = int(conversation_id)
    except (TypeError, ValueError):
        return _error("conversation_id must be an integer")

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       session_id::text AS session_id,
                       project_name,
                       project_path,
                       model,
                       entrypoint,
                       git_branch,
                       started_at,
                       ended_at,
                       message_count,
                       token_count_in,
                       token_count_out,
                       cost_usd,
                       summary,
                       tags,
                       metadata
                FROM conversations
                WHERE id = %s
                """,
                (conv_id,),
            )
            convs = _rows(cur)
            if not convs:
                return _error(f"conversation id {conv_id} not found")

            cur.execute(
                """
                SELECT id,
                       role::text AS role,
                       content,
                       tool_name,
                       token_count,
                       model,
                       created_at
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (conv_id,),
            )
            msgs = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok({"conversation": convs[0], "messages": msgs})


def tool_list_decisions(project: str | None = None, days: int = 30) -> dict[str, Any]:
    """All `category='decision'` memory chunks in chronological order.

    Args:
        project: Optional project name fragment (ILIKE). If omitted, all projects.
        days: How far back to look, in days. Default 30. Pass 0 for all-time.

    Returns: ok, count, decisions: list of {id, content, project, confidence,
    tags, created_at}.
    """
    params: list[Any] = []
    conds = ["status = 'active'", "category = 'decision'"]
    if project:
        conds.append("project_name ILIKE %s")
        params.append(f"%{project.strip()}%")
    try:
        days_int = int(days)
    except (TypeError, ValueError):
        days_int = 30
    if days_int > 0:
        conds.append("created_at >= %s")
        params.append(datetime.now(timezone.utc) - timedelta(days=days_int))

    try:
        with db_cursor() as cur:
            cur.execute(
                f"""
                SELECT id,
                       content,
                       project_name AS project,
                       confidence::float AS confidence,
                       tags,
                       created_at
                FROM memory_chunks
                WHERE {' AND '.join(conds)}
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok({"count": len(rows), "decisions": rows})


def tool_find_contact(name: str) -> dict[str, Any]:
    """Find memory chunks with category='contact' that mention `name`.

    Args:
        name: A full name, first name, or role fragment.

    Returns: ok, count, contacts: list of {id, content, project, tags,
    confidence, created_at}.
    """
    if not name or not name.strip():
        return _error("name must be a non-empty string")
    like = f"%{name.strip()}%"

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       content,
                       project_name AS project,
                       tags,
                       confidence::float AS confidence,
                       created_at
                FROM memory_chunks
                WHERE status = 'active'
                  AND category = 'contact'
                  AND (content ILIKE %s OR %s = ANY(tags))
                ORDER BY confidence DESC NULLS LAST, created_at DESC
                LIMIT 50
                """,
                (like, name.strip()),
            )
            chunks = _rows(cur)

            # Also search the entities table for person-type matches.
            canon = _canonicalize(name)
            cur.execute(
                """
                SELECT id, name, entity_type, project_name, mention_count,
                       first_seen, last_seen, attributes
                FROM entities
                WHERE entity_type = 'person'
                  AND (canonical_name = %s OR canonical_name ILIKE %s)
                ORDER BY mention_count DESC
                LIMIT 20
                """,
                (canon, f"%{canon}%"),
            )
            ents = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok({"count": len(chunks), "contacts": chunks, "entities": ents})


def tool_list_entities(
    entity_type: str | None = None, min_mentions: int = 3
) -> dict[str, Any]:
    """Knowledge-graph entities, ordered by `mention_count DESC`.

    Args:
        entity_type: Optional filter, e.g. "person", "project", "technology".
        min_mentions: Only return entities mentioned at least this many times.
                      Default 3.

    Returns: ok, count, entities: list of {id, name, entity_type,
    project_name, mention_count, first_seen, last_seen, attributes}.
    """
    try:
        mn = max(0, int(min_mentions))
    except (TypeError, ValueError):
        mn = 3

    conds = ["mention_count >= %s"]
    params: list[Any] = [mn]
    if entity_type:
        conds.append("entity_type = %s")
        params.append(entity_type.strip())

    try:
        with db_cursor() as cur:
            cur.execute(
                f"""
                SELECT id,
                       name,
                       entity_type,
                       project_name,
                       mention_count,
                       first_seen,
                       last_seen,
                       confidence::float AS confidence,
                       attributes
                FROM entities
                WHERE {' AND '.join(conds)}
                ORDER BY mention_count DESC, last_seen DESC
                LIMIT 200
                """,
                params,
            )
            rows = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok({"count": len(rows), "entities": rows})


def tool_get_entity_relations(entity_name: str) -> dict[str, Any]:
    """All incoming and outgoing relationships of an entity.

    Resolves `entity_name` against `canonical_name` (case/accent-insensitive),
    falling back to ILIKE if no exact match is found.

    Args:
        entity_name: Name or fragment of an entity.

    Returns: ok, entity (resolved row), outgoing: [{to_name, to_type,
    relation_type, valid_from, valid_until, confidence}], incoming: [...].
    """
    if not entity_name or not entity_name.strip():
        return _error("entity_name must be a non-empty string")
    canon = _canonicalize(entity_name)

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                SELECT id, name, entity_type, project_name, mention_count
                FROM entities
                WHERE canonical_name = %s
                ORDER BY mention_count DESC
                LIMIT 1
                """,
                (canon,),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    """
                    SELECT id, name, entity_type, project_name, mention_count
                    FROM entities
                    WHERE canonical_name ILIKE %s
                    ORDER BY mention_count DESC
                    LIMIT 1
                    """,
                    (f"%{canon}%",),
                )
                row = cur.fetchone()

            if not row:
                return _error(f"entity '{entity_name}' not found")

            entity = {k: _jsonable(v) for k, v in row.items()}
            eid = row["id"]

            cur.execute(
                """
                SELECT r.id,
                       e.name AS other_name,
                       e.entity_type AS other_type,
                       r.relation_type,
                       r.valid_from,
                       r.valid_until,
                       r.confidence::float AS confidence,
                       r.attributes
                FROM relationships r
                JOIN entities e ON e.id = r.to_entity
                WHERE r.from_entity = %s
                ORDER BY r.valid_from DESC NULLS LAST
                """,
                (eid,),
            )
            outgoing = _rows(cur)

            cur.execute(
                """
                SELECT r.id,
                       e.name AS other_name,
                       e.entity_type AS other_type,
                       r.relation_type,
                       r.valid_from,
                       r.valid_until,
                       r.confidence::float AS confidence,
                       r.attributes
                FROM relationships r
                JOIN entities e ON e.id = r.from_entity
                WHERE r.to_entity = %s
                ORDER BY r.valid_from DESC NULLS LAST
                """,
                (eid,),
            )
            incoming = _rows(cur)
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok({"entity": entity, "outgoing": outgoing, "incoming": incoming})


VALID_CATEGORIES = {
    "decision",
    "pattern",
    "insight",
    "preference",
    "contact",
    "error_solution",
    "project_context",
    "workflow",
}


def tool_add_memory(
    content: str,
    category: str,
    tags: list[str] | None = None,
    project: str | None = None,
    confidence: float = 0.8,
) -> dict[str, Any]:
    """Insert a new memory chunk. Use this from inside a Claude Code session
    whenever a reusable decision, pattern, insight, or preference is produced
    and the user agrees it should be remembered.

    Args:
        content:    The memory body (plain text, 1-4 sentences works best).
        category:   One of: decision, pattern, insight, preference, contact,
                    error_solution, project_context, workflow.
        tags:       Optional list of short tag strings for retrieval.
        project:    Optional project name to scope the memory.
        confidence: 0.0-1.0, default 0.8.

    Returns:
        ok, id: the new memory_chunks.id, category, created_at.
    """
    if not content or not content.strip():
        return _error("content must be a non-empty string")
    if not category or category not in VALID_CATEGORIES:
        return _error(
            f"category must be one of: {sorted(VALID_CATEGORIES)}",
            got=category,
        )
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.8
    conf = max(0.0, min(conf, 1.0))

    tag_list: list[str] = []
    if tags:
        if isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            try:
                tag_list = [str(t).strip() for t in tags if str(t).strip()]
            except TypeError:
                tag_list = []

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_chunks
                    (source_type, source_id, content, category, tags,
                     confidence, project_name, status)
                VALUES
                    ('mcp_tool', NULL, %s, %s::memory_category, %s, %s, %s, 'active')
                RETURNING id, created_at
                """,
                (content.strip(), category, tag_list, conf, project),
            )
            row = cur.fetchone()
    except DBError as exc:
        return _error(str(exc))
    except psycopg2.Error as exc:
        return _error(f"SQL error: {exc}")

    return _ok(
        {
            "id": int(row["id"]),
            "category": category,
            "project": project,
            "tags": tag_list,
            "confidence": conf,
            "created_at": _jsonable(row["created_at"]),
        }
    )


# ---------------------------------------------------------------------------
# FastMCP wiring.
# ---------------------------------------------------------------------------
def build_server() -> "FastMCP":
    if FastMCP is None:
        raise RuntimeError(
            "The `mcp` Python SDK is not installed. "
            "Run: pip install -r mcp/requirements.txt"
        )

    mcp = FastMCP("throughline")

    @mcp.tool()
    def search_memory(query: str, limit: int = 10) -> dict[str, Any]:
        """Full-text search over Throughline memory_chunks and messages.

        Fast, trigram-backed ILIKE search. Returns the highest-confidence
        memory chunks and the most recent messages whose content (or tags,
        or project) contains the query.
        """
        return tool_search_memory(query, limit)

    @mcp.tool()
    def search_semantic(query: str, limit: int = 10) -> dict[str, Any]:
        """Semantic (vector) search via pgvector cosine distance.

        Uses the embedding backend already configured for Throughline
        (OpenAI or Ollama). If no embeddings are present or the backend is
        unavailable, falls back to the same ILIKE search as `search_memory`
        and returns `fallback=True`.
        """
        return tool_search_semantic(query, limit)

    @mcp.tool()
    def get_project_context(project_name: str) -> dict[str, Any]:
        """Pull every memory chunk for a given project, grouped by category
        (decision / preference / pattern / contact / project_context / ...).

        Use this at the start of a session when the user says things like
        'what do I know about <project>?' or 'pull up the context for X'.
        """
        return tool_get_project_context(project_name)

    @mcp.tool()
    def get_recent_conversations(
        project: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """Most recent Claude Code sessions, optionally filtered by project.

        Returns session id, project, model, start/end timestamps, message
        count, and the auto-generated summary.
        """
        return tool_get_recent_conversations(project, limit)

    @mcp.tool()
    def get_conversation(conversation_id: int) -> dict[str, Any]:
        """Full transcript for a single conversation by numeric id.

        Includes session metadata and an ordered list of all messages (role,
        content, tool invocations, timestamps).
        """
        return tool_get_conversation(conversation_id)

    @mcp.tool()
    def list_decisions(project: str | None = None, days: int = 30) -> dict[str, Any]:
        """Chronological list of `decision` memory chunks.

        Useful for 'what did we decide about X?' or 'summarize this week's
        decisions'. `days=0` means all-time.
        """
        return tool_list_decisions(project, days)

    @mcp.tool()
    def find_contact(name: str) -> dict[str, Any]:
        """Contact lookup across memory chunks (category='contact') and the
        knowledge-graph `entities` table (entity_type='person').
        """
        return tool_find_contact(name)

    @mcp.tool()
    def list_entities(
        entity_type: str | None = None, min_mentions: int = 3
    ) -> dict[str, Any]:
        """Knowledge-graph entities (people, projects, technologies, ...).

        Ordered by mention_count. Useful for 'what do I work on most?' or
        'who do I talk to most about project X?'.
        """
        return tool_list_entities(entity_type, min_mentions)

    @mcp.tool()
    def get_entity_relations(entity_name: str) -> dict[str, Any]:
        """All incoming and outgoing relationships for an entity.

        Resolves the name canonically (case/accent-insensitive) and returns
        both directions with temporal validity (`valid_from`, `valid_until`).
        """
        return tool_get_entity_relations(entity_name)

    @mcp.tool()
    def add_memory(
        content: str,
        category: str,
        tags: list[str] | None = None,
        project: str | None = None,
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        """Persist a new memory chunk so future sessions can retrieve it.

        Category must be one of: decision, pattern, insight, preference,
        contact, error_solution, project_context, workflow.
        """
        return tool_add_memory(content, category, tags, project, confidence)

    return mcp


def main() -> int:
    try:
        server = build_server()
    except Exception as exc:
        log.error("failed to build MCP server: %s", exc)
        return 1

    log.info(
        "Throughline MCP server starting (db=%s@%s:%s/%s)",
        DB_CONFIG.get("user"),
        DB_CONFIG.get("host"),
        DB_CONFIG.get("port"),
        DB_CONFIG.get("dbname"),
    )

    # Sanity-ping the DB so startup fails loudly rather than on the first
    # tool invocation. Non-fatal: the server still starts so the client can
    # see a sensible error from individual tool calls.
    try:
        with db_cursor() as cur:
            cur.execute("SELECT 1")
        log.info("db connection OK")
    except DBError as exc:
        log.warning("db unreachable at startup — tools will return errors: %s", exc)

    # FastMCP.run() blocks on stdio until the client disconnects.
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
