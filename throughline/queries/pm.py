"""Virtual Team Ops: catalogs, relationships, assignments, tasks, events.

One file for the whole `pm_*` domain rather than one per table — the tables
are small and the domain is one bounded feature (see
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md), not the kind
of independently-evolving areas that justify projects.py's own file.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from ._exec import Row, execute, one, rows, scalar

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


# ── Assignments ──────────────────────────────────────────────────────────────


def create_assignment(
    conn,
    *,
    pm_project_id: int,
    team_id: int,
    role_id: int,
    member_id: int,
    ai_tool: str | None = None,
    ai_model: str | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        """
        INSERT INTO pm_assignments
            (pm_project_id, team_id, role_id, member_id, ai_tool, ai_model)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (pm_project_id, team_id, role_id, member_id, ai_tool, ai_model),
    )
    conn.commit()
    return row


def resolve_assignment(conn, assignment_id: int) -> dict[str, Any]:
    """The effective ai_tool/model/skills/instructions/budgets for one
    assignment: role default with a member override for AI binding, a union
    of skills/documents, and role-then-member concatenated instructions
    (role's general mandate first, the member's individual flavor after —
    per docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md §3)."""
    row = one(
        conn,
        """
        SELECT
            a.id, a.ai_tool AS override_tool, a.ai_model AS override_model,
            r.default_ai_tool, r.default_ai_model,
            r.skill_refs AS role_skills, r.instructions AS role_instructions,
            r.document_refs AS role_documents, r.token_budget AS role_budget,
            m.skill_refs AS member_skills, m.instructions AS member_instructions,
            m.document_refs AS member_documents, m.token_budget AS member_budget,
            t.token_budget AS team_budget,
            p.token_budget AS project_budget
        FROM pm_assignments a
        JOIN pm_roles r ON r.id = a.role_id
        JOIN pm_members m ON m.id = a.member_id
        JOIN pm_teams t ON t.id = a.team_id
        JOIN pm_projects p ON p.id = a.pm_project_id
        WHERE a.id = %s
        """,
        (assignment_id,),
    )

    if row is None:
        raise ValueError(f"No assignment with id {assignment_id}")

    role_skills = list(row["role_skills"] or [])
    member_skills = list(row["member_skills"] or [])
    merged_skills = sorted(set(role_skills) | set(member_skills))

    role_docs = list(row["role_documents"] or [])
    member_docs = list(row["member_documents"] or [])
    seen = set(role_docs)
    merged_docs = list(role_docs)
    for d in member_docs:
        if d not in seen:
            seen.add(d)
            merged_docs.append(d)

    parts = [p for p in (row["role_instructions"], row["member_instructions"]) if p]
    instructions = "\n\n".join(parts) if parts else None

    return {
        "assignment_id": row["id"],
        "ai_tool": row["override_tool"] or row["default_ai_tool"],
        "ai_model": row["override_model"] or row["default_ai_model"],
        "skill_refs": merged_skills,
        "instructions": instructions,
        "document_refs": merged_docs,
        "role_budget": row["role_budget"],
        "member_budget": row["member_budget"],
        "team_budget": row["team_budget"],
        "project_budget": row["project_budget"],
    }


# ── Tasks and events ─────────────────────────────────────────────────────────


def create_task(
    conn,
    *,
    pm_project_id: int,
    team_id: int,
    title: str,
    run_id: str,
    repo_path: str,
    log_dir: str,
    pid: int | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        """
        INSERT INTO pm_tasks
            (pm_project_id, team_id, title, run_id, repo_path, log_dir, pid)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (pm_project_id, team_id, title, run_id, repo_path, log_dir, pid),
    )
    conn.commit()
    return row


def get_task(conn, task_id: int) -> dict[str, Any]:
    row = one(conn, "SELECT * FROM pm_tasks WHERE id = %s", (task_id,))
    if row is None:
        raise ValueError(f"No task with id {task_id}")
    return row


def list_running_tasks(conn) -> list[Row]:
    return rows(conn, "SELECT * FROM pm_tasks WHERE status = 'running' ORDER BY started_at")


def list_tasks_for_project(conn, pm_project_id: int) -> list[Row]:
    return rows(
        conn,
        """
        SELECT * FROM pm_tasks WHERE pm_project_id = %s
        ORDER BY (status = 'running') DESC, created_at DESC
        """,
        (pm_project_id,),
    )


def add_task_event(
    conn,
    *,
    task_id: int,
    step: str,
    event_type: str,
    assignment_id: int | None = None,
    iteration: int | None = None,
    message: str | None = None,
    detail_path: str | None = None,
    tokens_used: int | None = None,
) -> dict[str, Any]:
    row = one(
        conn,
        """
        INSERT INTO pm_task_events
            (task_id, assignment_id, step, iteration, event_type, message,
             detail_path, tokens_used)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (task_id, assignment_id, step, iteration, event_type, message,
         detail_path, tokens_used),
    )
    conn.commit()
    return row


def recompute_task_tokens(conn, task_id: int) -> int:
    """`pm_tasks.tokens_used` is always derived, never written directly
    elsewhere, so it can never drift from the events it is a sum of."""
    total = scalar(
        conn,
        """
        UPDATE pm_tasks SET tokens_used = COALESCE((
            SELECT SUM(tokens_used) FROM pm_task_events
            WHERE task_id = %s AND tokens_used IS NOT NULL
        ), 0)
        WHERE id = %s
        RETURNING tokens_used
        """,
        (task_id, task_id),
    )
    conn.commit()
    return int(total)


def set_task_status(conn, task_id: int, status: str) -> None:
    """Transition a task's status, stamping `started_at`/`ended_at` on the
    way in/out of flight. No caller currently needs to set those timestamps
    explicitly, so this takes no override parameters for them."""
    if status == "running":
        execute(
            conn,
            "UPDATE pm_tasks SET status = %s, started_at = COALESCE(started_at, now()) "
            "WHERE id = %s",
            (status, task_id),
        )
    elif status in ("pass", "fail", "budget_exceeded", "crashed", "stopped"):
        execute(
            conn,
            "UPDATE pm_tasks SET status = %s, ended_at = COALESCE(ended_at, now()) "
            "WHERE id = %s",
            (status, task_id),
        )
    else:
        execute(conn, "UPDATE pm_tasks SET status = %s WHERE id = %s", (status, task_id))
    conn.commit()


def budgets_for_task(conn, task_id: int) -> dict[str, int | None]:
    """The applicable project/team budgets for this task, plus the
    strictest role and member budget among every assignment its events have
    referenced so far. Any of the four may be None (unbounded)."""
    row = one(
        conn,
        """
        SELECT p.token_budget AS project_budget, t.token_budget AS team_budget
        FROM pm_tasks tk
        JOIN pm_projects p ON p.id = tk.pm_project_id
        JOIN pm_teams t ON t.id = tk.team_id
        WHERE tk.id = %s
        """,
        (task_id,),
    )
    project_budget = row["project_budget"] if row else None
    team_budget = row["team_budget"] if row else None

    budget_row = one(
        conn,
        """
        SELECT MIN(r.token_budget) AS role_budget, MIN(m.token_budget) AS member_budget
        FROM pm_task_events e
        JOIN pm_assignments a ON a.id = e.assignment_id
        JOIN pm_roles r ON r.id = a.role_id
        JOIN pm_members m ON m.id = a.member_id
        WHERE e.task_id = %s
        """,
        (task_id,),
    )

    return {
        "project_budget": project_budget,
        "team_budget": team_budget,
        "role_budget": budget_row["role_budget"] if budget_row else None,
        "member_budget": budget_row["member_budget"] if budget_row else None,
    }
