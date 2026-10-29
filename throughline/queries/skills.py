"""Queries over ``skills``, ``projects`` and ``prompts``.

These three are small catalogue tables that the UI treats the same way
(list, filter, sort, open detail), so they share a module rather than three
near-empty ones.
"""

from __future__ import annotations

from typing import Any

from ._exec import Row, one, rows

# ── skills ───────────────────────────────────────────────────────────────────


def list_skills(conn, limit: int = 500) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, name, description, use_count, last_used, version, path, triggers,
               COALESCE(file_modified, last_used, created_at) AS sort_date
        FROM skills
        ORDER BY sort_date DESC NULLS LAST, use_count DESC NULLS LAST, name
        LIMIT %s
        """,
        (limit,),
    )


def get_skill(conn, skill_id: int) -> Row | None:
    return one(
        conn,
        """
        SELECT id, name, version, description, path, triggers, last_used, use_count,
               config, created_at, updated_at, file_created, file_modified
        FROM skills WHERE id = %s
        """,
        (skill_id,),
    )


# ── projects ─────────────────────────────────────────────────────────────────

#: Whitelisted sort keys. Interpolating a caller string into ORDER BY is only
#: safe because the value must be a key of this mapping — an unknown key
#: raises rather than falling back to a default, so a typo surfaces as an
#: error instead of a silently mis-sorted table.
PROJECT_SORTS: dict[str, str] = {
    "last_activity": "last_activity",
    "created_at": "p.created_at",
    "name": "p.name",
    "chunks_count": "chunks_count",
    "conversations_count": "conversations_count",
    "status": "p.status",
}

#: Sorts that read most naturally ascending. Everything else defaults to
#: descending (most recent / largest first).
_ASCENDING_BY_DEFAULT = frozenset({"name", "status"})


def list_projects(conn, sort: str = "last_activity", descending: bool | None = None) -> list[Row]:
    key = PROJECT_SORTS.get(sort)
    if key is None:
        raise ValueError(f"unknown project sort {sort!r}; known: {sorted(PROJECT_SORTS)}")
    if descending is None:
        descending = sort not in _ASCENDING_BY_DEFAULT
    direction = "DESC" if descending else "ASC"
    return rows(
        conn,
        f"""
        SELECT
            p.id,
            p.name,
            p.description,
            p.status::text                                AS status,
            p.created_at,
            COALESCE(mc.chunks_count, 0)                  AS chunks_count,
            COALESCE(cv.conversations_count, 0)           AS conversations_count,
            GREATEST(mc.last_activity, cv.last_activity)  AS last_activity,
            LEAST(mc.first_activity, cv.first_activity)   AS first_activity
        FROM projects p
        LEFT JOIN (
            SELECT project_name,
                   count(*)        AS chunks_count,
                   max(created_at) AS last_activity,
                   min(created_at) AS first_activity
            FROM memory_chunks
            WHERE project_name IS NOT NULL
            GROUP BY project_name
        ) mc ON mc.project_name = p.name
        LEFT JOIN (
            SELECT project_name,
                   count(*)        AS conversations_count,
                   max(started_at) AS last_activity,
                   min(started_at) AS first_activity
            FROM conversations
            -- Per-project rollups shown beside a project's own record; they
            -- must agree with the project view, which counts human sessions.
            WHERE project_name IS NOT NULL AND generated_by IS NULL
            GROUP BY project_name
        ) cv ON cv.project_name = p.name
        ORDER BY {key} {direction} NULLS LAST, p.name ASC
        """,
    )


def observed_project_names(conn) -> list[Row]:
    """Project names that appear in the data, whether or not `projects` has a row.

    The `projects` table is currently empty on some installs while dozens of
    distinct `project_name` values exist across conversations and memory
    chunks. Any surface that offers a project facet should use this rather
    than `list_projects`, which is limited to registered rows.
    """
    return rows(
        conn,
        """
        SELECT name,
               sum(chunks)        AS chunks_count,
               sum(conversations) AS conversations_count,
               max(last_activity) AS last_activity
        FROM (
            SELECT project_name AS name, count(*) AS chunks, 0 AS conversations,
                   max(created_at) AS last_activity
            FROM memory_chunks WHERE project_name IS NOT NULL GROUP BY project_name
            UNION ALL
            SELECT project_name, 0, count(*), max(started_at)
            FROM conversations WHERE project_name IS NOT NULL GROUP BY project_name
        ) u
        GROUP BY name
        ORDER BY last_activity DESC NULLS LAST, name
        """,
    )


def get_project(conn, project_id: int) -> Row | None:
    return one(
        conn,
        """
        SELECT id, name, description, contacts, decisions, status::text AS status,
               created_at, updated_at
        FROM projects WHERE id = %s
        """,
        (project_id,),
    )


# ── prompts ──────────────────────────────────────────────────────────────────

PROMPT_SORTS: dict[str, str] = {
    "created_newest": "created_at DESC NULLS LAST, id DESC",
    "created_oldest": "created_at ASC NULLS LAST, id ASC",
    "updated_newest": "COALESCE(updated_at, created_at) DESC NULLS LAST, id DESC",
    "used_most": "usage_count DESC NULLS LAST, name",
    "name_az": "name ASC",
}


def prompt_categories(conn) -> list[str]:
    return [
        r["category"]
        for r in rows(
            conn,
            "SELECT DISTINCT category FROM prompts WHERE category IS NOT NULL ORDER BY category",
        )
    ]


def list_prompts(
    conn,
    category: str | None = None,
    search: str | None = None,
    sort: str = "created_newest",
    limit: int = 500,
) -> list[Row]:
    order = PROMPT_SORTS.get(sort)
    if order is None:
        raise ValueError(f"unknown prompt sort {sort!r}; known: {sorted(PROMPT_SORTS)}")

    clauses: list[str] = []
    params: list[Any] = []
    if category and category != "All":
        clauses.append("category = %s")
        params.append(category)
    if search:
        clauses.append("(name ILIKE %s OR content ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    return rows(
        conn,
        f"""
        SELECT id, name, category, usage_count, tags, content, created_at, updated_at
        FROM prompts
        {where}
        ORDER BY {order}
        LIMIT %s
        """,
        [*params, limit],
    )


def get_prompt(conn, prompt_id: int) -> Row | None:
    return one(
        conn,
        """
        SELECT id, name, category, content, variables, source_path, usage_count,
               tags, created_at, updated_at
        FROM prompts WHERE id = %s
        """,
        (prompt_id,),
    )


def get_project_by_name(conn, name: str) -> Row | None:
    """A project keyed by *name*, whether or not it is registered.

    A project's identity is its name — that is what ``conversations`` and
    ``memory_chunks`` reference. The ``projects`` table is *enrichment*
    (description, status, contacts), not the source of truth, and on a typical
    install it lags well behind reality: 53 registered rows against 81 observed
    names on the author's database.

    Keying detail on ``projects.id`` therefore made every unregistered project
    unreachable — memory attributed to it existed and was searchable, but the
    project itself did not resolve. This merges the observed activity with the
    registry row when there is one.
    """
    return one(
        conn,
        """
        WITH observed AS (
            SELECT name,
                   sum(chunks)::bigint        AS chunks_count,
                   sum(conversations)::bigint AS conversations_count,
                   max(last_activity)         AS last_activity,
                   min(first_activity)        AS first_activity
            FROM (
                SELECT project_name AS name, count(*) AS chunks, 0 AS conversations,
                       max(created_at) AS last_activity, min(created_at) AS first_activity
                FROM memory_chunks WHERE project_name = %(name)s GROUP BY project_name
                UNION ALL
                SELECT project_name, 0, count(*), max(started_at), min(started_at)
                FROM conversations
                WHERE project_name = %(name)s AND generated_by IS NULL
                GROUP BY project_name
            ) u GROUP BY name
        )
        SELECT COALESCE(o.name, p.name)              AS name,
               p.id,
               p.description,
               p.status::text                        AS status,
               p.contacts,
               p.created_at,
               COALESCE(o.chunks_count, 0)           AS chunks_count,
               COALESCE(o.conversations_count, 0)    AS conversations_count,
               o.last_activity,
               o.first_activity,
               (p.id IS NOT NULL)                    AS registered
        FROM observed o
        FULL OUTER JOIN projects p ON p.name = o.name AND p.name = %(name)s
        WHERE COALESCE(o.name, p.name) = %(name)s
        LIMIT 1
        """,
        {"name": name},
    )
