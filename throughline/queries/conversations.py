"""Queries over ``conversations`` and ``messages``."""

from __future__ import annotations

from typing import Any

from ._exec import Row, one, rows, scalar


def distinct_projects(conn) -> list[str]:
    return [r["project_name"] for r in rows(
        conn,
        "SELECT DISTINCT project_name FROM conversations "
        "WHERE project_name IS NOT NULL ORDER BY project_name",
    )]


def distinct_models(conn) -> list[str]:
    return [r["model"] for r in rows(
        conn,
        "SELECT DISTINCT model FROM conversations WHERE model IS NOT NULL ORDER BY model",
    )]


def _filters(
    project: str | None = None,
    model: str | None = None,
    message_search: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project and project != "All":
        clauses.append("c.project_name = %s")
        params.append(project)
    if model and model != "All":
        clauses.append("c.model = %s")
        params.append(model)
    if message_search:
        clauses.append(
            "c.id IN (SELECT DISTINCT conversation_id FROM messages WHERE content ILIKE %s)"
        )
        params.append(f"%{message_search}%")
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_conversations(
    conn,
    project: str | None = None,
    model: str | None = None,
    message_search: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[Row]:
    where, params = _filters(project, model, message_search)
    return rows(
        conn,
        f"""
        SELECT c.id, c.summary AS title, c.project_name AS project, c.model,
               c.started_at, c.message_count AS messages
        FROM conversations c
        {where}
        ORDER BY c.started_at DESC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    )


def count_conversations(
    conn,
    project: str | None = None,
    model: str | None = None,
    message_search: str | None = None,
) -> int:
    where, params = _filters(project, model, message_search)
    return int(scalar(conn, f"SELECT count(*) FROM conversations c {where}", params, 0) or 0)


def recent(conn, limit: int = 5) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, summary, project_name, model, started_at, message_count
        FROM conversations
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def get_conversation(conn, conversation_id: int) -> Row | None:
    return one(
        conn,
        """
        SELECT id, session_id, project_path, project_name, model, entrypoint,
               git_branch, started_at, ended_at, message_count,
               token_count_in, token_count_out, cost_usd, summary, tags, metadata
        FROM conversations
        WHERE id = %s
        """,
        (conversation_id,),
    )


def messages_for(
    conn,
    conversation_id: int,
    ascending: bool = True,
    limit: int = 1000,
    offset: int = 0,
) -> list[Row]:
    direction = "ASC" if ascending else "DESC"
    return rows(
        conn,
        f"""
        SELECT id, role::text AS role, content, tool_name, token_count,
               model, duration_ms, created_at, is_sidechain
        FROM messages
        WHERE conversation_id = %s
        ORDER BY created_at {direction}, id {direction}
        LIMIT %s OFFSET %s
        """,
        (conversation_id, limit, offset),
    )


def chunks_from_conversation(conn, conversation_id: int) -> list[Row]:
    """Memory chunks that were extracted out of this conversation."""
    return rows(
        conn,
        """
        SELECT id, category::text AS category, content, confidence, tags, created_at
        FROM memory_chunks
        WHERE source_type = 'conversation' AND source_id = %s
        ORDER BY created_at DESC
        """,
        (conversation_id,),
    )


def totals(conn) -> Row:
    return one(
        conn,
        """
        SELECT
            (SELECT count(*) FROM conversations)  AS conv,
            (SELECT count(*) FROM messages)       AS msg,
            (SELECT count(*) FROM skills)         AS sk,
            (SELECT count(*) FROM memory_chunks)  AS mem
        """,
    ) or {"conv": 0, "msg": 0, "sk": 0, "mem": 0}
