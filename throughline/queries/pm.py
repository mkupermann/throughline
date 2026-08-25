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


# ── Project / team / role relationships ─────────────────────────────────────


def create_pm_project(
    conn, *, name: str, description: str | None = None, token_budget: int | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        "INSERT INTO pm_projects (name, description, token_budget) "
        "VALUES (%s, %s, %s) RETURNING *",
        (name, description, token_budget),
    )
    conn.commit()
    return row


def list_pm_projects(conn) -> list[Row]:
    return rows(conn, "SELECT * FROM pm_projects ORDER BY created_at DESC")


def link_project_repo(conn, pm_project_id: int, project_id: int) -> None:
    execute(
        conn,
        "INSERT INTO pm_project_repos (pm_project_id, project_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (pm_project_id, project_id),
    )
    conn.commit()


def link_project_team(conn, pm_project_id: int, team_id: int) -> None:
    execute(
        conn,
        "INSERT INTO pm_project_teams (pm_project_id, team_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (pm_project_id, team_id),
    )
    conn.commit()


def link_team_role(conn, team_id: int, role_id: int) -> None:
    execute(
        conn,
        "INSERT INTO pm_team_roles (team_id, role_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (team_id, role_id),
    )
    conn.commit()


def get_project_teams(conn, pm_project_id: int) -> list[dict[str, Any]]:
    """Teams linked to a project, each with its linked roles nested under `roles`."""
    teams = rows(
        conn,
        """
        SELECT t.* FROM pm_teams t
        JOIN pm_project_teams pt ON pt.team_id = t.id
        WHERE pt.pm_project_id = %s
        ORDER BY t.name
        """,
        (pm_project_id,),
    )
    for team in teams:
        team["roles"] = rows(
            conn,
            """
            SELECT r.* FROM pm_roles r
            JOIN pm_team_roles tr ON tr.role_id = r.id
            WHERE tr.team_id = %s
            ORDER BY r.name
            """,
            (team["id"],),
        )
    return teams
