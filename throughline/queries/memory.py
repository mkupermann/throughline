"""Queries over ``memory_chunks`` — browse, filter, detail, curation queues."""

from __future__ import annotations

from typing import Any

from ._exec import Row, one, rows, scalar
from .projects import project_filter_params, project_filter_sql

CATEGORIES = (
    "decision",
    "pattern",
    "insight",
    "preference",
    "contact",
    "error_solution",
    "project_context",
    "workflow",
)

#: Status filter values the UI offers. ``active`` treats a NULL status as
#: active, matching the column default; ``all`` applies no status predicate.
STATUS_FILTERS = ("active", "all", "superseded", "merged", "stale")


def _build_filters(
    category: str | None = None,
    project: str | None = None,
    search: str | None = None,
    status: str = "active",
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if category and category != "All":
        if category not in CATEGORIES:
            raise ValueError(f"unknown category {category!r}")
        clauses.append("category = %s")
        params.append(category)
    if project:
        clauses.append("project_name ILIKE %s")
        params.append(f"%{project}%")
    if search:
        clauses.append("content ILIKE %s")
        params.append(f"%{search}%")

    if status == "active":
        clauses.append("COALESCE(status, 'active') = 'active'")
    elif status != "all":
        if status not in STATUS_FILTERS:
            raise ValueError(f"unknown status filter {status!r}")
        clauses.append("status = %s")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_chunks(
    conn,
    category: str | None = None,
    project: str | None = None,
    search: str | None = None,
    status: str = "active",
    limit: int = 300,
    offset: int = 0,
) -> list[Row]:
    where, params = _build_filters(category, project, search, status)
    return rows(
        conn,
        f"""
        SELECT id, category::text AS category, content, confidence,
               project_name, tags, created_at, status, access_count, last_accessed
        FROM memory_chunks
        {where}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    )


def count_chunks(
    conn,
    category: str | None = None,
    project: str | None = None,
    search: str | None = None,
    status: str = "active",
) -> int:
    where, params = _build_filters(category, project, search, status)
    return int(scalar(conn, f"SELECT count(*) FROM memory_chunks {where}", params, 0) or 0)


def project_context_knowledge(conn, project: str, *, include_generated: bool = False) -> list[Row]:
    """Active knowledge with provenance and the transcript's automation filter.

    Only ``conversation`` sources point at a conversation. Manual writes,
    consolidation results, and reflections keep their own polymorphic source
    meaning and therefore remain visible.
    """
    return rows(
        conn,
        f"""
        SELECT mc.id, 'memory' AS type, mc.category::text AS category, mc.content,
               mc.confidence, mc.source_type,
               CASE WHEN mc.source_type = 'conversation'
                    THEN source_conversation.id
                    ELSE mc.source_id
               END AS source_id
        FROM memory_chunks mc
        LEFT JOIN conversations source_conversation
          ON mc.source_type = 'conversation'
         AND source_conversation.id = mc.source_id
        WHERE (
            (mc.source_type = 'conversation'
             AND source_conversation.id IS NOT NULL
             AND {project_filter_sql("source_conversation")})
            OR
            ((mc.source_type <> 'conversation' OR source_conversation.id IS NULL)
             AND mc.project_name = %(project)s)
          )
          AND COALESCE(mc.status, 'active') = 'active'
          AND (
            %(include_generated)s
            OR mc.source_type <> 'conversation'
            OR source_conversation.generated_by IS NULL
          )
        ORDER BY mc.category ASC, mc.created_at ASC, mc.id ASC
        """,
        {**project_filter_params(project), "include_generated": include_generated},
    )


def get_chunk(conn, chunk_id: int) -> Row | None:
    return one(
        conn,
        """
        SELECT id, source_type, source_id, content, category::text AS category,
               tags, confidence, project_name, expires_at, created_at,
               superseded_by, superseded_at, status, merged_from,
               access_count, last_accessed
        FROM memory_chunks
        WHERE id = %s
        """,
        (chunk_id,),
    )


def category_counts(conn) -> list[Row]:
    return rows(
        conn,
        """
        SELECT category::text AS category, count(*) AS n
        FROM memory_chunks
        GROUP BY category
        ORDER BY n DESC
        """,
    )


def status_counts(conn) -> Row:
    return (
        one(
            conn,
            """
        SELECT
          COUNT(*) FILTER (WHERE COALESCE(status,'active')='active') AS active,
          COUNT(*) FILTER (WHERE status='superseded')                AS superseded,
          COUNT(*) FILTER (WHERE status='merged')                    AS merged,
          COUNT(*) FILTER (WHERE status='stale')                     AS stale,
          COUNT(*)                                                   AS total
        FROM memory_chunks
        """,
        )
        or {"active": 0, "superseded": 0, "merged": 0, "stale": 0, "total": 0}
    )


def most_accessed(conn, limit: int = 10, snippet: int = 120) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, category::text AS category, substring(content, 1, %s) AS content,
               access_count, last_accessed
        FROM memory_chunks
        WHERE COALESCE(access_count, 0) > 0
          AND COALESCE(status, 'active') = 'active'
        ORDER BY access_count DESC, last_accessed DESC NULLS LAST
        LIMIT %s
        """,
        (snippet, limit),
    )


def supersede_links(conn, limit: int = 100, snippet: int = 80) -> list[Row]:
    return rows(
        conn,
        """
        SELECT mc.id, mc.status, mc.superseded_by, mc.merged_from,
               substring(mc.content, 1, %s) AS content, mc.created_at
        FROM memory_chunks mc
        WHERE mc.status IN ('superseded', 'merged')
           OR (mc.merged_from IS NOT NULL AND array_length(mc.merged_from, 1) > 0)
        ORDER BY mc.created_at DESC
        LIMIT %s
        """,
        (snippet, limit),
    )


def reflections(conn, limit: int = 50, snippet: int = 200) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, reflection_type, action_taken,
               array_length(affected_chunks, 1) AS n_chunks,
               affected_chunks, confidence, created_at,
               substring(reasoning, 1, %s) AS reasoning
        FROM memory_reflections
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (snippet, limit),
    )


def reflection_summary(conn) -> list[Row]:
    return rows(
        conn,
        """
        SELECT reflection_type,
               COUNT(*) AS n,
               COUNT(*) FILTER (
                   WHERE action_taken LIKE 'merged%%'
                      OR action_taken LIKE '%%superseded%%'
                      OR action_taken IN ('marked_stale', 'created_super_chunk')
               ) AS mutated
        FROM memory_reflections
        GROUP BY reflection_type
        ORDER BY n DESC
        """,
    )


# ── Curation queues (the /curate surface) ────────────────────────────────────


def queue_low_confidence(conn, threshold: float = 0.6, limit: int = 100) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, category::text AS category, substring(content, 1, 200) AS content,
               confidence, project_name, created_at
        FROM memory_chunks
        WHERE COALESCE(status, 'active') = 'active' AND confidence < %s
        ORDER BY confidence ASC, created_at DESC
        LIMIT %s
        """,
        (threshold, limit),
    )


def queue_missing_embeddings(conn, model: str | None = None, limit: int = 100) -> list[Row]:
    return rows(
        conn,
        """
        SELECT mc.id, mc.category::text AS category,
               substring(mc.content, 1, 200) AS content, mc.project_name, mc.created_at
        FROM memory_chunks mc
        WHERE COALESCE(mc.status, 'active') = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM embeddings e
              WHERE e.source_type = 'memory_chunk'
                AND e.source_id = mc.id
                AND (%s IS NULL OR e.model = %s)
          )
        ORDER BY mc.created_at DESC
        LIMIT %s
        """,
        (model, model, limit),
    )


def queue_expiring(conn, limit: int = 100) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, category::text AS category, substring(content, 1, 200) AS content,
               expires_at, project_name
        FROM memory_chunks
        WHERE expires_at IS NOT NULL
          AND COALESCE(status, 'active') = 'active'
        ORDER BY expires_at ASC
        LIMIT %s
        """,
        (limit,),
    )


def queue_never_accessed(conn, older_than_days: int = 30, limit: int = 100) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, category::text AS category, substring(content, 1, 200) AS content,
               project_name, created_at
        FROM memory_chunks
        WHERE COALESCE(access_count, 0) = 0
          AND COALESCE(status, 'active') = 'active'
          AND created_at < now() - make_interval(days => %s)
        ORDER BY created_at ASC
        LIMIT %s
        """,
        (older_than_days, limit),
    )


def insert_chunk(
    conn,
    content: str,
    category: str,
    project_name: str | None = None,
    tags: list[str] | None = None,
    confidence: float = 0.8,
    source_type: str = "manual",
) -> int:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    return int(
        scalar(
            conn,
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, tags, confidence, project_name)
            VALUES (%s, NULL, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (source_type, content, category, tags or [], confidence, project_name),
        )
    )
