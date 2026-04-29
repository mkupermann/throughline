"""Semantic search helpers for the Streamlit GUI.

Reuses backend logic from scripts/generate_embeddings.py so embedding/search
logic stays in one place.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

# scripts/ importierbar machen
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from generate_embeddings import (  # type: ignore
    pick_backend,
    Backend,
    OllamaBackend,
    OpenAIBackend,
    ollama_up,
    ollama_has_model,
    OLLAMA_MODEL,
)


_BACKEND: Optional[Backend] = None


def get_backend(preferred: str = "auto") -> Optional[Backend]:
    """Lazy-init backend, fails soft (returns None if unavailable)."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        _BACKEND = pick_backend(preferred)
        return _BACKEND
    except SystemExit:
        return None
    except Exception:
        return None


def backend_available() -> bool:
    return get_backend() is not None


def backend_label() -> str:
    b = get_backend()
    if b is None:
        return "unavailable"
    return f"{b.name}/{b.model} ({b.dim}d)"


def embed_text(text: str) -> Optional[List[float]]:
    b = get_backend()
    if b is None:
        return None
    try:
        v = b.embed([text[: b.max_chars]])[0]
        if len(v) != b.dim:
            return None
        return v
    except Exception:
        return None


def vec_literal(vec: List[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def count_embeddings(conn) -> int:
    b = get_backend()
    if b is None:
        return 0
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM embeddings WHERE model = %s", (b.model,))
        return int(cur.fetchone()[0])


def semantic_search(conn, query: str, limit: int = 20, project: str | None = None) -> list:
    """Returns list of dicts: source_type, source_id, content, category, project_name, distance.

    When project is given, results are restricted at the SQL layer to that
    project_name (memory_chunks.project_name and conversations.project_name).
    """
    import psycopg2.extras

    b = get_backend()
    if b is None:
        return []
    vec = embed_text(query)
    if vec is None:
        return []
    lit = vec_literal(vec)
    col = b.column
    mc_proj = "AND mc.project_name = %s" if project else ""
    ms_proj = "AND c.project_name = %s" if project else ""
    sql = f"""
        WITH mc AS (
            SELECT 'memory_chunk'::text AS source_type,
                   mc.id AS source_id,
                   mc.content,
                   mc.category::text AS category,
                   mc.project_name,
                   mc.confidence::float AS confidence,
                   NULL::bigint AS conversation_id,
                   e.{col} <=> %s::vector AS distance
            FROM embeddings e
            JOIN memory_chunks mc ON mc.id = e.source_id
            WHERE e.source_type = 'memory_chunk'
              AND e.model = %s AND e.{col} IS NOT NULL
              {mc_proj}
        ),
        ms AS (
            SELECT 'message'::text AS source_type,
                   m.id AS source_id,
                   m.content,
                   m.role::text AS category,
                   c.project_name,
                   NULL::float AS confidence,
                   m.conversation_id,
                   e.{col} <=> %s::vector AS distance
            FROM embeddings e
            JOIN messages m ON m.id = e.source_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE e.source_type = 'message'
              AND e.model = %s AND e.{col} IS NOT NULL
              {ms_proj}
        )
        SELECT * FROM (
            SELECT * FROM mc
            UNION ALL
            SELECT * FROM ms
        ) x
        ORDER BY distance ASC
        LIMIT %s
    """
    params: list = [lit, b.model]
    if project:
        params.append(project)
    params += [lit, b.model]
    if project:
        params.append(project)
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def similar_to_source(conn, source_type: str, source_id: int, limit: int = 8) -> list:
    """Find items similar to a given DB row (excludes itself)."""
    import psycopg2.extras

    b = get_backend()
    if b is None:
        return []
    col = b.column
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT {col} FROM embeddings WHERE source_type=%s AND source_id=%s AND model=%s",
            (source_type, source_id, b.model),
        )
        row = cur.fetchone()
        if not row or row[col] is None:
            return []
        vec = row[col]  # pgvector returns as list in newer psycopg; else str
        if isinstance(vec, str):
            # e.g. '[0.01,...]'
            lit = vec
        elif isinstance(vec, list):
            lit = vec_literal(vec)
        else:
            # psycopg2 fallback: cast via SQL
            lit = None

        base_sql = f"""
            WITH mc AS (
                SELECT 'memory_chunk'::text AS source_type, mc.id AS source_id,
                       mc.content, mc.category::text AS category, mc.project_name,
                       NULL::bigint AS conversation_id,
                       e.{col} <=> %s::vector AS distance
                FROM embeddings e JOIN memory_chunks mc ON mc.id = e.source_id
                WHERE e.source_type='memory_chunk' AND e.model=%s AND e.{col} IS NOT NULL
                  AND NOT (e.source_type=%s AND e.source_id=%s)
            ),
            ms AS (
                SELECT 'message'::text AS source_type, m.id AS source_id,
                       m.content, m.role::text AS category, c.project_name,
                       m.conversation_id,
                       e.{col} <=> %s::vector AS distance
                FROM embeddings e JOIN messages m ON m.id=e.source_id
                JOIN conversations c ON c.id=m.conversation_id
                WHERE e.source_type='message' AND e.model=%s AND e.{col} IS NOT NULL
                  AND NOT (e.source_type=%s AND e.source_id=%s)
            )
            SELECT * FROM (SELECT * FROM mc UNION ALL SELECT * FROM ms) x
            ORDER BY distance ASC LIMIT %s
        """
        cur.execute(
            base_sql,
            (lit, b.model, source_type, source_id,
             lit, b.model, source_type, source_id,
             limit),
        )
        return [dict(r) for r in cur.fetchall()]
