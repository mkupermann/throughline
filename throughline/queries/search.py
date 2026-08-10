"""Lexical search across every record type.

Extracted verbatim (behaviour-preserving) from ``gui/page_views/search.py``.
Each scope is a standalone function so the HTTP API can fan them out and the
GUI can keep its per-scope expanders, and ``search_all`` runs the set.

The trigram indexes ``idx_memory_content_trgm`` / ``idx_messages_content_trgm``
back the ILIKE predicates on the two large tables.
"""

from __future__ import annotations

from typing import Callable

from ._exec import Row, rows

DEFAULT_SNIPPET = 200


def _like(term: str) -> str:
    return f"%{term}%"


def search_conversations(conn, term: str, limit: int = 20) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, summary, project_name, started_at, message_count
        FROM conversations
        WHERE summary ILIKE %s OR project_name ILIKE %s
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (like, like, limit),
    )


def search_messages(conn, term: str, limit: int = 30, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT m.id,
               m.conversation_id,
               c.summary AS titel,
               m.role::text AS role,
               substring(m.content, 1, %s) AS snippet,
               m.created_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.content ILIKE %s
        ORDER BY m.created_at DESC
        LIMIT %s
        """,
        (snippet, like, limit),
    )


def search_memory(conn, term: str, limit: int = 30, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id,
               category::text AS category,
               substring(content, 1, %s) AS content,
               confidence,
               project_name,
               tags
        FROM memory_chunks
        WHERE content ILIKE %s OR %s = ANY(tags) OR project_name ILIKE %s
        ORDER BY confidence DESC
        LIMIT %s
        """,
        (snippet, like, term, like, limit),
    )


def search_skills(conn, term: str, limit: int = 20, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, name, substring(description, 1, %s) AS description, use_count
        FROM skills
        WHERE name ILIKE %s OR description ILIKE %s
        ORDER BY COALESCE(file_modified, last_used, created_at) DESC NULLS LAST
        LIMIT %s
        """,
        (snippet, like, like, limit),
    )


def search_projects(conn, term: str, limit: int = 20) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, name, description, status::text AS status
        FROM projects
        WHERE name ILIKE %s OR description ILIKE %s
        ORDER BY created_at DESC NULLS LAST
        LIMIT %s
        """,
        (like, like, limit),
    )


def search_prompts(conn, term: str, limit: int = 20, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, name, category, substring(content, 1, %s) AS content, tags
        FROM prompts
        WHERE name ILIKE %s OR content ILIKE %s OR category ILIKE %s
        ORDER BY created_at DESC NULLS LAST
        LIMIT %s
        """,
        (snippet, like, like, like, limit),
    )


#: Scope name -> query function. The GUI, the CLI and the API all iterate this
#: rather than hard-coding the list of searchable record types.
SCOPES: dict[str, Callable[..., list[Row]]] = {
    "conversations": search_conversations,
    "messages": search_messages,
    "memory": search_memory,
    "skills": search_skills,
    "projects": search_projects,
    "prompts": search_prompts,
}

DEFAULT_LIMITS: dict[str, int] = {
    "conversations": 20,
    "messages": 30,
    "memory": 30,
    "skills": 20,
    "projects": 20,
    "prompts": 20,
}


def search_all(
    conn,
    term: str,
    scopes: list[str] | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, list[Row]]:
    """Run every requested scope and return ``{scope: rows}``.

    Unknown scope names raise, rather than being silently dropped — a typo in
    a caller must not look like "no results".
    """
    if not term:
        return {}
    wanted = list(SCOPES) if scopes is None else list(scopes)
    unknown = [s for s in wanted if s not in SCOPES]
    if unknown:
        raise ValueError(f"unknown search scope(s): {unknown}; known: {sorted(SCOPES)}")

    caps = dict(DEFAULT_LIMITS)
    if limits:
        caps.update(limits)

    return {scope: SCOPES[scope](conn, term, limit=caps[scope]) for scope in wanted}
