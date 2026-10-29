"""Vector (HNSW) retrieval over ``embeddings``.

Query shape — and why it is what it is
--------------------------------------
An open finding in the project notes claimed these CTEs "bypass the HNSW index
(full scan)" because the distance is projected inside a CTE and the sort is
applied to the outer union. That is the classic pgvector anti-pattern, so the
obvious fix is to push ``ORDER BY <col> <=> :vec LIMIT n`` directly onto
``embeddings`` per branch and join afterwards.

**That fix was implemented, measured, and reverted.** On PostgreSQL 16 with
pgvector 0.8.2, seeded with 50 000 embeddings:

===========================  ==========================  =======  ========
case                         plan                        rows     time
===========================  ==========================  =======  ========
unfiltered, this shape       HNSW index scan             20       0.39 ms
unfiltered, pushed-down      HNSW index scan             20       0.16 ms
project-filtered, this shape HNSW + iterative filter     **20**   0.79 ms
project-filtered, pushed-down HNSW capped, then filtered **2**    13.9 ms
===========================  ==========================  =======  ========

PostgreSQL inlines these CTEs and pushes the ordering down by itself, so the
index *is* used. Worse, the "fixed" version caps the candidate set before the
project predicate is applied, so a selective filter silently returns a
fraction of the requested rows — a correctness bug traded for no speed.

The lesson is narrow and worth keeping: **do not add a bounding LIMIT above a
filter the planner can push into the index scan.** If a future pgvector or
planner regression does break this, the fix is per-branch pushdown *plus*
``hnsw.iterative_scan``, not pushdown alone — and it must be re-measured on a
filtered query, not just an unfiltered one.

What did change in the move out of ``gui/semantic_helper.py``: the embedding
column is validated against a whitelist instead of being interpolated raw, and
the module no longer imports an embedding backend — callers pass the query
vector in, so nothing here touches the network.
"""

from __future__ import annotations

from collections.abc import Sequence

from ._exec import Row, check_embedding_column, rows, scalar


def supports_iterative_scan(conn) -> bool:
    """True when the server exposes pgvector's ``hnsw.iterative_scan`` GUC.

    ``current_setting(..., missing_ok => true)`` returns NULL for an unknown
    parameter instead of raising, which makes this a cheap, side-effect-free
    probe. Not used by the queries below; kept for the diagnostics surface.
    """
    try:
        return scalar(conn, "SELECT current_setting('hnsw.iterative_scan', true)") is not None
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def semantic_search(
    conn,
    vector_literal: str,
    model: str,
    column: str,
    limit: int = 20,
    project: str | None = None,
) -> list[Row]:
    """Nearest-neighbour search over memory chunks and messages.

    *vector_literal* is the pgvector text form of the query embedding
    (``"[0.1,0.2,...]"``); embedding the query text is the caller's job so
    that this module stays free of backend/network concerns.
    """
    col = check_embedding_column(column)
    sql = f"""
        WITH mc AS (
            SELECT 'memory_chunk'::text AS source_type,
                   mc.id                AS source_id,
                   mc.content,
                   mc.category::text    AS category,
                   mc.project_name,
                   mc.confidence::float AS confidence,
                   NULL::bigint         AS conversation_id,
                   e.{col} <=> %(vec)s::vector AS distance
            FROM embeddings e
            JOIN memory_chunks mc ON mc.id = e.source_id
            WHERE e.source_type = 'memory_chunk'
              AND e.model = %(model)s
              AND e.{col} IS NOT NULL
              AND COALESCE(mc.status,'active') <> 'forgotten'
              AND (%(project)s::text IS NULL OR mc.project_name = %(project)s)
        ),
        ms AS (
            SELECT 'message'::text AS source_type,
                   m.id            AS source_id,
                   m.content,
                   m.role::text    AS category,
                   c.project_name,
                   NULL::float     AS confidence,
                   m.conversation_id,
                   e.{col} <=> %(vec)s::vector AS distance
            FROM embeddings e
            JOIN messages m      ON m.id = e.source_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE e.source_type = 'message'
              AND e.model = %(model)s
              AND e.{col} IS NOT NULL
              AND (%(project)s::text IS NULL OR c.project_name = %(project)s)
        )
        SELECT * FROM (SELECT * FROM mc UNION ALL SELECT * FROM ms) x
        ORDER BY distance ASC
        LIMIT %(limit)s
    """
    return rows(
        conn,
        sql,
        {"vec": vector_literal, "model": model, "project": project, "limit": limit},
    )


def similar_to_source(
    conn,
    source_type: str,
    source_id: int,
    model: str,
    column: str,
    limit: int = 8,
) -> list[Row]:
    """Items nearest to an existing row, excluding the row itself.

    The probe vector is read with a scalar subquery so it never round-trips
    through Python — the previous implementation fetched the vector, guessed
    at its Python representation (``str`` vs ``list`` vs unknown) and could
    end up passing ``None`` as the literal.
    """
    col = check_embedding_column(column)
    sql = f"""
        WITH src AS (
            SELECT {col} AS vec
            FROM embeddings
            WHERE source_type = %(stype)s AND source_id = %(sid)s AND model = %(model)s
              AND {col} IS NOT NULL
            LIMIT 1
        ),
        mc AS (
            SELECT 'memory_chunk'::text AS source_type, mc.id AS source_id,
                   mc.content, mc.category::text AS category, mc.project_name,
                   NULL::bigint AS conversation_id,
                   e.{col} <=> (SELECT vec FROM src) AS distance
            FROM embeddings e
            JOIN memory_chunks mc ON mc.id = e.source_id
            WHERE e.source_type = 'memory_chunk'
              AND e.model = %(model)s
              AND e.{col} IS NOT NULL
              AND NOT (e.source_type = %(stype)s AND e.source_id = %(sid)s)
              AND EXISTS (SELECT 1 FROM src)
        ),
        ms AS (
            SELECT 'message'::text AS source_type, m.id AS source_id,
                   m.content, m.role::text AS category, c.project_name,
                   m.conversation_id,
                   e.{col} <=> (SELECT vec FROM src) AS distance
            FROM embeddings e
            JOIN messages m      ON m.id = e.source_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE e.source_type = 'message'
              AND e.model = %(model)s
              AND e.{col} IS NOT NULL
              AND NOT (e.source_type = %(stype)s AND e.source_id = %(sid)s)
              AND EXISTS (SELECT 1 FROM src)
        )
        SELECT * FROM (SELECT * FROM mc UNION ALL SELECT * FROM ms) x
        ORDER BY distance ASC
        LIMIT %(limit)s
    """
    return rows(
        conn,
        sql,
        {"stype": source_type, "sid": source_id, "model": model, "limit": limit},
    )


def count_embeddings(conn, model: str) -> int:
    return int(scalar(conn, "SELECT COUNT(*) FROM embeddings WHERE model = %s", (model,), 0) or 0)


def vec_literal(vec: Sequence[float]) -> str:
    """Render a Python float sequence as a pgvector literal."""
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def explain_search(
    conn,
    vector_literal: str,
    model: str,
    column: str,
    limit: int = 20,
    project: str | None = None,
    analyze: bool = False,
) -> str:
    """EXPLAIN (optionally ANALYZE) for :func:`semantic_search`.

    A diagnostic for the /operate surface, not a test assertion. Whether the
    planner picks the HNSW index is a cost decision that flips with table
    size and filter selectivity, so asserting on it in CI produces a flaky
    test rather than a useful guard — see this module's docstring.
    """
    col = check_embedding_column(column)
    prefix = "EXPLAIN (ANALYZE)" if analyze else "EXPLAIN"
    plan = rows(
        conn,
        f"""
        {prefix}
        SELECT mc.id, e.{col} <=> %(vec)s::vector AS distance
        FROM embeddings e
        JOIN memory_chunks mc ON mc.id = e.source_id
        WHERE e.source_type = 'memory_chunk'
          AND e.model = %(model)s
          AND e.{col} IS NOT NULL
          AND (%(project)s::text IS NULL OR mc.project_name = %(project)s)
        ORDER BY distance ASC
        LIMIT %(limit)s
        """,
        {"vec": vector_literal, "model": model, "project": project, "limit": limit},
    )
    return "\n".join(str(r["QUERY PLAN"]) for r in plan)


__all__ = [
    "semantic_search",
    "similar_to_source",
    "count_embeddings",
    "vec_literal",
    "supports_iterative_scan",
    "explain_search",
]
