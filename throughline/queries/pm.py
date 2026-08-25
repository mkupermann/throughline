"""Virtual Team Ops: catalogs, relationships, assignments, tasks, events.

One file for the whole `pm_*` domain rather than one per table — the tables
are small and the domain is one bounded feature (see
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md), not the kind
of independently-evolving areas that justify projects.py's own file.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from ._exec import Row, execute, one, rows

# ── Catalogs ─────────────────────────────────────────────────────────────────


def create_role(
    conn,
    *,
    name: str,
    description: str | None = None,
    default_ai_tool: str | None = None,
    default_ai_model: str | None = None,
    skill_refs: list[int] | None = None,
    instructions: str | None = None,
    document_refs: list[str] | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        """
        INSERT INTO pm_roles
            (name, description, default_ai_tool, default_ai_model,
             skill_refs, instructions, document_refs, token_budget)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            name, description, default_ai_tool, default_ai_model,
            skill_refs or [], instructions,
            Json(document_refs or []), token_budget,
        ),
    )
    conn.commit()
    return row


def list_roles(conn) -> list[Row]:
    return rows(conn, "SELECT * FROM pm_roles ORDER BY name")


def create_member(
    conn,
    *,
    name: str,
    member_type: str,
    contact_info: dict[str, Any] | None = None,
    skill_refs: list[int] | None = None,
    instructions: str | None = None,
    document_refs: list[str] | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        """
        INSERT INTO pm_members
            (name, member_type, contact_info, skill_refs, instructions,
             document_refs, token_budget)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            name, member_type, Json(contact_info or {}), skill_refs or [],
            instructions, Json(document_refs or []), token_budget,
        ),
    )
    conn.commit()
    return row


def list_members(conn) -> list[Row]:
    return rows(conn, "SELECT * FROM pm_members ORDER BY name")


def create_team(
    conn, *, name: str, description: str | None = None, token_budget: int | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        "INSERT INTO pm_teams (name, description, token_budget) "
        "VALUES (%s, %s, %s) RETURNING *",
        (name, description, token_budget),
    )
    conn.commit()
    return row


def list_teams(conn) -> list[Row]:
    return rows(conn, "SELECT * FROM pm_teams ORDER BY name")
