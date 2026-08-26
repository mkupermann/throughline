"""Virtual Team Ops: catalogs, relationships, assignments, tasks, events.

One file for the whole `pm_*` domain rather than one per table — the tables
are small and the domain is one bounded feature (see
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md), not the kind
of independently-evolving areas that justify projects.py's own file.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
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


# ── Repo projects (existing memory-layer projects, surfaced as PM catalog) ──
#
# `projects` (120 curated rows) and `conversations` (per-session aggregates,
# see throughline/queries/projects.py's module docstring) are joined on
# projects.name = conversations.project_name — conversations.project_name is
# a generated column (sql/schema.sql), the last path segment of
# conversations.project_path. pm_project_repos is the bridge table that lets
# one such repo project be adopted as (or linked to) a pm_projects row.
#
# A repo project can be linked to at most one pm_project in practice —
# enforced here in application code (adopt_repo_project's pre-check), not by
# a DB constraint, since pm_project_repos' primary key is
# (pm_project_id, project_id) and permits a project_id to appear under
# several pm_project_ids at the schema level.


class RepoProjectAlreadyLinked(Exception):
    """Raised by adopt_repo_project when the target repo project already has
    a pm_project_repos row — the router turns this into a 409 rather than
    silently creating a second pm_project for the same repo."""


#: Sessions/last-activity/most-recent-repo-path for one repo project, as a
#: single LATERAL aggregate — a plain GROUP BY join would fan out once
#: `projects` and `conversations` are joined together with the
#: pm_project_repos/pm_projects link, inflating the session count.
#: `array_agg(... ORDER BY started_at DESC)[1]` picks the project_path of the
#: single most recent conversation without a second round trip.
_REPO_PROJECT_AGG = """
    LEFT JOIN LATERAL (
        SELECT
            count(*) AS sessions,
            max(co.started_at) AS last_active,
            (array_agg(co.project_path ORDER BY co.started_at DESC))[1] AS repo_path
        FROM conversations co
        WHERE co.project_name = p.name
    ) agg ON true
"""


def list_repo_projects(conn, limit: int = 200) -> list[Row]:
    """Every curated `projects` row enriched with its conversations
    aggregates and (if any) its pm_project_repos link — the source list for
    the dashboard's "repository projects" section and the adopt flow.
    Ordered by last activity, most recent first, unlinked-and-inactive rows
    trailing at the end."""
    return rows(
        conn,
        f"""
        SELECT
            p.id, p.name, p.description, p.status,
            COALESCE(agg.sessions, 0)::bigint AS sessions,
            agg.last_active,
            agg.repo_path,
            link.pm_project_id AS linked_pm_project_id,
            link.pm_project_name AS linked_pm_project_name
        FROM projects p
        {_REPO_PROJECT_AGG}
        LEFT JOIN LATERAL (
            SELECT pr.pm_project_id, pp.name AS pm_project_name
            FROM pm_project_repos pr
            JOIN pm_projects pp ON pp.id = pr.pm_project_id
            WHERE pr.project_id = p.id
            ORDER BY pr.pm_project_id
            LIMIT 1
        ) link ON true
        ORDER BY agg.last_active DESC NULLS LAST, p.name
        LIMIT %s
        """,
        (limit,),
    )


def list_repos_for_pm_project(conn, pm_project_id: int) -> list[Row]:
    """Repo projects linked to *pm_project_id*, in the same enriched shape as
    list_repo_projects — for the cockpit's linked-repos chips."""
    return rows(
        conn,
        f"""
        SELECT
            p.id, p.name, p.description, p.status,
            COALESCE(agg.sessions, 0)::bigint AS sessions,
            agg.last_active,
            agg.repo_path,
            pr.pm_project_id AS linked_pm_project_id,
            pp.name AS linked_pm_project_name
        FROM pm_project_repos pr
        JOIN projects p ON p.id = pr.project_id
        JOIN pm_projects pp ON pp.id = pr.pm_project_id
        {_REPO_PROJECT_AGG}
        WHERE pr.pm_project_id = %s
        ORDER BY agg.last_active DESC NULLS LAST, p.name
        """,
        (pm_project_id,),
    )


def adopt_repo_project(conn, project_id: int) -> dict[str, Any]:
    """Create a new pm_project named after *project_id* (a `projects` row)
    and link it, in one step — the dashboard's "adopt as PM project" action.

    Refuses (RepoProjectAlreadyLinked) when the repo project is already
    linked to some pm_project rather than creating a duplicate; unknown
    *project_id* raises ValueError (404 at the router)."""
    project = one(conn, "SELECT id, name, description FROM projects WHERE id = %s", (project_id,))
    if project is None:
        raise ValueError(f"no repo project with id {project_id}")

    existing = one(
        conn, "SELECT pm_project_id FROM pm_project_repos WHERE project_id = %s", (project_id,)
    )
    if existing is not None:
        raise RepoProjectAlreadyLinked(
            f"repo project {project_id} is already linked to "
            f"pm_project {existing['pm_project_id']}"
        )

    pm_project = create_pm_project(conn, name=project["name"], description=project["description"])
    link_project_repo(conn, pm_project["id"], project_id)
    return pm_project


def unlink_project_repo(conn, pm_project_id: int, project_id: int) -> bool:
    """Remove one pm_project<->repo-project link. Returns False (no
    exception) if no such link exists, so the router can turn that into a
    404."""
    affected = execute(
        conn,
        "DELETE FROM pm_project_repos WHERE pm_project_id = %s AND project_id = %s",
        (pm_project_id, project_id),
    )
    conn.commit()
    return affected > 0


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


def list_assignments_for_project(conn, pm_project_id: int) -> list[Row]:
    """Every assignment for one project, with the role/member names joined
    in — the Zuordnungs-Matrix wants both the raw ids (to key off of) and
    the human-readable names (to render) without a second round trip."""
    return rows(
        conn,
        """
        SELECT
            a.id, a.team_id, a.role_id, a.member_id, a.ai_tool, a.ai_model,
            r.name AS role_name, m.name AS member_name
        FROM pm_assignments a
        JOIN pm_roles r ON r.id = a.role_id
        JOIN pm_members m ON m.id = a.member_id
        WHERE a.pm_project_id = %s
        ORDER BY a.id
        """,
        (pm_project_id,),
    )


def delete_assignment(conn, assignment_id: int) -> bool:
    """Remove one assignment. Returns False (no exception) if *assignment_id*
    does not exist, so the router can turn that into a 404 without a
    round-trip SELECT first."""
    affected = execute(conn, "DELETE FROM pm_assignments WHERE id = %s", (assignment_id,))
    conn.commit()
    return affected > 0


# ── Deletes (catalog + project + task) ──────────────────────────────────────
#
# Every function here is a plain DELETE that returns whether a row existed
# (False, no exception, means the router turns that into a 404). None of
# these catch psycopg2.IntegrityError themselves — a FK RESTRICT violation
# (pm_tasks.pm_project_id/team_id have no ON DELETE clause; pm_task_events
# .assignment_id likewise) is left to propagate so the router can catch it
# the same way /pm/tasks/register already does, roll the connection back,
# and answer 409 with a message naming what still references the row.


def delete_pm_project(conn, project_id: int) -> bool:
    affected = execute(conn, "DELETE FROM pm_projects WHERE id = %s", (project_id,))
    conn.commit()
    return affected > 0


def delete_team(conn, team_id: int) -> bool:
    affected = execute(conn, "DELETE FROM pm_teams WHERE id = %s", (team_id,))
    conn.commit()
    return affected > 0


def delete_role(conn, role_id: int) -> bool:
    affected = execute(conn, "DELETE FROM pm_roles WHERE id = %s", (role_id,))
    conn.commit()
    return affected > 0


def delete_member(conn, member_id: int) -> bool:
    affected = execute(conn, "DELETE FROM pm_members WHERE id = %s", (member_id,))
    conn.commit()
    return affected > 0


def delete_task(conn, task_id: int) -> bool:
    affected = execute(conn, "DELETE FROM pm_tasks WHERE id = %s", (task_id,))
    conn.commit()
    return affected > 0


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


def get_skill_names(conn, ids: list[int]) -> list[str]:
    """Resolve numeric skills.id values (as stored in a role/member's
    skill_refs) to their human-readable names, for embedding into a launched
    agent's context file."""
    if not ids:
        return []
    return [
        r["name"]
        for r in rows(conn, "SELECT name FROM skills WHERE id = ANY(%s) ORDER BY name", (ids,))
    ]


def overview(conn) -> dict[str, Any]:
    """Aggregate figures for the /pm dashboard: per-project team count, task
    counts by status, tokens_used/token_budget, and last activity, plus
    catalog-wide counts. Two subquery aggregates (teams, tasks) rather than
    a single multi-join query — joining pm_project_teams and pm_tasks
    directly on pm_projects would fan out (one row per team-task pair per
    project), inflating both the token sum and the task counts."""
    project_rows = rows(
        conn,
        """
        SELECT
            p.id, p.name, p.status, p.token_budget,
            COALESCE(tk.tokens_used_sum, 0)::bigint AS tokens_used,
            COALESCE(tm.teams_count, 0)::bigint AS teams,
            COALESCE(tk.running, 0)::bigint AS running,
            COALESCE(tk.pass, 0)::bigint AS pass,
            COALESCE(tk.fail, 0)::bigint AS fail,
            COALESCE(tk.budget_exceeded, 0)::bigint AS budget_exceeded,
            COALESCE(tk.crashed, 0)::bigint AS crashed,
            COALESCE(tk.stopped, 0)::bigint AS stopped,
            COALESCE(tk.pending, 0)::bigint AS pending,
            tk.last_activity
        FROM pm_projects p
        LEFT JOIN (
            SELECT pm_project_id, COUNT(*) AS teams_count
            FROM pm_project_teams
            GROUP BY pm_project_id
        ) tm ON tm.pm_project_id = p.id
        LEFT JOIN (
            SELECT
                pm_project_id,
                SUM(tokens_used) AS tokens_used_sum,
                COUNT(*) FILTER (WHERE status = 'running') AS running,
                COUNT(*) FILTER (WHERE status = 'pass') AS pass,
                COUNT(*) FILTER (WHERE status = 'fail') AS fail,
                COUNT(*) FILTER (WHERE status = 'budget_exceeded') AS budget_exceeded,
                COUNT(*) FILTER (WHERE status = 'crashed') AS crashed,
                COUNT(*) FILTER (WHERE status = 'stopped') AS stopped,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                MAX(COALESCE(ended_at, created_at)) AS last_activity
            FROM pm_tasks
            GROUP BY pm_project_id
        ) tk ON tk.pm_project_id = p.id
        ORDER BY p.created_at DESC
        """,
    )

    projects = [
        {
            "id": r["id"],
            "name": r["name"],
            "status": r["status"],
            "token_budget": r["token_budget"],
            "tokens_used": r["tokens_used"],
            "teams": r["teams"],
            "tasks": {
                "running": r["running"],
                "pass": r["pass"],
                "fail": r["fail"],
                "budget_exceeded": r["budget_exceeded"],
                "crashed": r["crashed"],
                "stopped": r["stopped"],
                "pending": r["pending"],
            },
            "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None,
        }
        for r in project_rows
    ]

    counts = one(
        conn,
        """
        SELECT
            (SELECT COUNT(*) FROM pm_roles)         AS roles,
            (SELECT COUNT(*) FROM pm_members)       AS members,
            (SELECT COUNT(*) FROM pm_teams)         AS teams,
            (SELECT COUNT(*) FROM pm_ai_providers)  AS models
        """,
    )

    return {"projects": projects, "counts": counts}


def list_skills(conn) -> list[Row]:
    """Lean {id, name, description} listing, ordered by name, for the PM
    skills picker (item 5 of
    docs/superpowers/specs/2026-08-26-virtual-team-ops-ui-rebuild.md).
    Deliberately not throughline.queries.skills.list_skills, which returns
    many more fields sorted by recency for a different UI.

    scan_skills indexes the same skill name from multiple filesystem paths
    (project-local, user-level, plugin-provided, ...), which left the
    underlying skills table with many rows sharing one name. DISTINCT ON
    collapses those down to a single row per name, preferring whichever
    path was modified most recently."""
    return rows(
        conn,
        """
        SELECT id, name, description
        FROM (
            SELECT DISTINCT ON (name) id, name, description
            FROM skills
            ORDER BY name, file_modified DESC NULLS LAST
        ) deduped
        ORDER BY name
        """,
    )


# ── AI binding catalog ───────────────────────────────────────────────────────
#
# The Role/Member editors used to accept ai_tool/ai_model as free text — a
# typo (or a model name that quietly stopped being pulled) only surfaced as a
# launch-time failure. This resolves, at request time, what each tool the
# pipeline actually understands (see throughline/jobs/pm_launch.py) can
# currently be pointed at, so the frontend can offer selects instead. Every
# source degrades to an empty/static list rather than failing the whole
# endpoint — one tool being unreachable should not block picking another.

def _normalize_ollama_url(raw: str) -> str:
    """Ollama's own convention allows OLLAMA_HOST to be a bare host
    ("127.0.0.1"), host:port, or a full URL — the Windows installer sets the
    bare-host form system-wide. Normalize to a full base URL with Ollama's
    default port so urlopen gets something valid."""
    value = raw.strip().rstrip("/")
    if not value:
        return "http://127.0.0.1:11434"
    if "://" not in value:
        value = f"http://{value}"
    scheme, _, rest = value.partition("://")
    if ":" not in rest:
        value = f"{scheme}://{rest}:11434"
    return value


_OLLAMA_URL = _normalize_ollama_url(os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
_OLLAMA_TIMEOUT_S = 2.0

#: Vibe's built-in --agent profiles, always valid even before any
#: role/member-derived profile has been written to ~/.vibe/agents/ by
#: ensure_vibe_agent_profile.
_VIBE_BUILTIN_AGENTS = ("ask", "plan", "accept-edits", "auto-approve")


def _ollama_models(base_url: str | None = None) -> tuple[list[str], bool]:
    """(model names as aider/LiteLLM expects them, prefixed "ollama_chat/",
    unavailable). A short timeout and a broad except: a slow or unreachable
    Ollama must never hang or fail this request — it just means aider has no
    models to offer right now.

    *base_url* defaults to the process-wide Ollama daemon (OLLAMA_HOST,
    resolved once at import time) for the built-in "aider" tool. A
    pm_ai_providers row of type 'ollama' passes its own base_url override
    instead (refresh_provider_models / ai_catalog), normalized the same way.
    """
    url = _normalize_ollama_url(base_url) if base_url else _OLLAMA_URL
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=_OLLAMA_TIMEOUT_S) as resp:
            if resp.status != 200:
                return [], True
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return [], True
    names = sorted({str(m["name"]) for m in data.get("models", []) if m.get("name")})
    return [f"ollama_chat/{n}" for n in names], False


def _vibe_agent_profiles() -> list[str]:
    """Stems of ~/.vibe/agents/*.toml (role/member-derived profiles written
    by ensure_vibe_agent_profile) plus vibe's static builtins, deduplicated
    and sorted. A missing ~/.vibe/agents/ directory is not an error — it
    just means no profile has been generated yet."""
    agents_dir = Path.home() / ".vibe" / "agents"
    try:
        written = {p.stem for p in agents_dir.glob("*.toml")}
    except OSError:
        written = set()
    return sorted(written | set(_VIBE_BUILTIN_AGENTS))


# ── AI providers (Welle D) ────────────────────────────────────────────────
#
# Cline/Cursor-style provider & model management: a user adds a STANDARD
# provider (name, type, optional base URL, API key) whose model list is
# fetched live from the provider itself, plus CUSTOM model ids of their own.
# Both are folded into ai_catalog() below as one extra "provider:<id>" tool
# entry per enabled row, and throughline/jobs/pm_launch.py resolves that
# same "provider:<id>" ai_tool at launch time to inject the right
# credentials into the spawned pipeline's environment.

_PROVIDER_TYPES = (
    "openai", "anthropic", "mistral", "google", "openrouter", "ollama", "openai_compatible",
)

#: Base URL used when a provider row leaves base_url empty. ollama/
#: openai_compatible have no sensible default (enforced as required at the
#: router) and are absent here on purpose.
_PROVIDER_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
}

#: LiteLLM/aider model-string prefix per provider type — this is what makes
#: a model chosen through the Role editor actually runnable by the pipeline
#: (see pm_launch.py's AI_PIPELINE_EXECUTOR_MODEL).
_PROVIDER_LITELLM_PREFIX = {
    "openai": "openai/",
    "anthropic": "anthropic/",
    "mistral": "mistral/",
    "google": "gemini/",
    "openrouter": "openrouter/",
    "ollama": "ollama_chat/",
    "openai_compatible": "openai/",
}

_PROVIDER_TIMEOUT_S = 5.0


def create_ai_provider(
    conn,
    *,
    name: str,
    provider_type: str,
    base_url: str | None = None,
    api_key: str | None = None,
    custom_models: list[str] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    row = one(
        conn,
        """
        INSERT INTO pm_ai_providers (name, provider_type, base_url, api_key, custom_models, enabled)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (name, provider_type, base_url, api_key, Json(custom_models or []), enabled),
    )
    conn.commit()
    return row


def list_ai_providers(conn) -> list[Row]:
    return rows(conn, "SELECT * FROM pm_ai_providers ORDER BY name")


def get_ai_provider(conn, provider_id: int) -> dict[str, Any] | None:
    return one(conn, "SELECT * FROM pm_ai_providers WHERE id = %s", (provider_id,))


def delete_ai_provider(conn, provider_id: int) -> bool:
    affected = execute(conn, "DELETE FROM pm_ai_providers WHERE id = %s", (provider_id,))
    conn.commit()
    return affected > 0


_PROVIDER_COLUMNS = frozenset({
    "name", "provider_type", "base_url", "api_key", "custom_models", "enabled",
})
_PROVIDER_JSON_COLUMNS = frozenset({"custom_models"})


def update_ai_provider(conn, provider_id: int, **fields: Any) -> dict[str, Any] | None:
    return _update_row(conn, "pm_ai_providers", provider_id, fields, _PROVIDER_COLUMNS, _PROVIDER_JSON_COLUMNS)


def _fetch_json_models(
    url: str, headers: dict[str, str], extract: Callable[[Any], list[Any]]
) -> tuple[list[str], bool, str | None]:
    """GET *url* with *headers*, parse the JSON body and run *extract* over
    it to pull out raw model ids. Returns (ids, unavailable, error) — a
    broad except on every network/parse failure, same policy as
    _ollama_models: an unreachable or misconfigured provider must never 500
    this request, only report itself unavailable with a reason."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_PROVIDER_TIMEOUT_S) as resp:
            if resp.status != 200:
                return [], True, f"HTTP {resp.status}"
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return [], True, str(exc)
    try:
        ids = sorted({str(x) for x in extract(data) if x})
    except (KeyError, TypeError) as exc:
        return [], True, f"unexpected response shape: {exc}"
    return ids, False, None


def _provider_model_ids(provider: dict[str, Any]) -> tuple[list[str], bool, str | None]:
    """(raw model ids — no litellm prefix, unavailable, error) for one
    pm_ai_providers row, fetched live from the provider itself. Callers
    that need the litellm-format strings apply _PROVIDER_LITELLM_PREFIX
    themselves (ai_catalog, refresh_provider_models) — kept separate so a
    caller can also compare/union raw ids against custom_models, which are
    stored unprefixed too."""
    ptype = provider["provider_type"]
    base_url = (provider.get("base_url") or "").strip().rstrip("/")
    api_key = provider.get("api_key") or ""

    if ptype == "ollama":
        models, unavailable = _ollama_models(base_url or None)
        ids = [m.removeprefix("ollama_chat/") for m in models]
        return ids, unavailable, ("Ollama nicht erreichbar" if unavailable else None)

    if ptype == "anthropic":
        url = (base_url or _PROVIDER_DEFAULT_BASE["anthropic"]) + "/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        return _fetch_json_models(url, headers, lambda d: [m.get("id") for m in d.get("data", [])])

    if ptype == "google":
        base = base_url or _PROVIDER_DEFAULT_BASE["google"]
        url = f"{base}/v1beta/models?key={urllib.parse.quote(api_key)}"
        return _fetch_json_models(
            url, {},
            lambda d: [str(m.get("name", "")).removeprefix("models/") for m in d.get("models", [])],
        )

    # openai, mistral, openrouter, openai_compatible: all OpenAI-shaped
    # `GET {base}/v1/models` (or `{base}/models` when base already ends in
    # /v1, e.g. a custom OpenAI-compatible gateway configured with its own
    # /v1 suffix already included).
    base = base_url or _PROVIDER_DEFAULT_BASE.get(ptype, "")
    if not base:
        return [], True, "no base_url configured"
    path = "/models" if base.endswith("/v1") else "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return _fetch_json_models(base + path, headers, lambda d: [m.get("id") for m in d.get("data", [])])


def refresh_provider_models(conn, provider_id: int) -> dict[str, Any]:
    """Live-fetch one provider's model list — POST /pm/ai-providers/{id}/
    models/refresh. Raises ValueError (404 at the router) for an unknown
    provider id; every other failure (network, auth, bad response shape)
    comes back as {"unavailable": True, "error": "..."} rather than an
    exception, matching _provider_model_ids' own contract."""
    provider = get_ai_provider(conn, provider_id)
    if provider is None:
        raise ValueError(f"no provider with id {provider_id}")
    ids, unavailable, error = _provider_model_ids(provider)
    prefix = _PROVIDER_LITELLM_PREFIX[provider["provider_type"]]
    return {"models": [f"{prefix}{m}" for m in ids], "unavailable": unavailable, "error": error}


def ai_catalog(conn) -> dict[str, Any]:
    """{"tools": [{tool, label, models, unavailable}, ...]} — the real,
    currently-resolvable AI tool/model choices for the Role/Member editors'
    selects. The three built-in tools (aider/claude/vibe) need no database
    access at all — they come from the local Ollama daemon, the local
    ~/.vibe/agents/ directory, and a static entry for Claude Code (the
    pipeline calls `claude -p` with no model choice today). *conn* is only
    needed for the user-defined pm_ai_providers rows appended after them
    (Welle D)."""
    ollama_models, ollama_unavailable = _ollama_models()
    tools: list[dict[str, Any]] = [
        {
            "tool": "aider",
            "label": "Aider + Ollama (lokal)",
            "models": ollama_models,
            "unavailable": ollama_unavailable,
        },
        {
            "tool": "claude",
            "label": "Claude Code",
            "models": ["claude -p (Standard)"],
            "unavailable": False,
        },
        {
            "tool": "vibe",
            "label": "Vibe",
            "models": _vibe_agent_profiles(),
            "unavailable": False,
        },
    ]
    for provider in list_ai_providers(conn):
        if not provider["enabled"]:
            continue
        live_ids, unavailable, _error = _provider_model_ids(provider)
        prefix = _PROVIDER_LITELLM_PREFIX[provider["provider_type"]]
        models = sorted({f"{prefix}{m}" for m in live_ids} | {f"{prefix}{m}" for m in provider["custom_models"]})
        tools.append({
            "tool": f"provider:{provider['id']}",
            "label": f"{provider['name']} ({provider['provider_type']})",
            "models": models,
            "unavailable": unavailable,
            "provider_id": provider["id"],
        })
    return {"tools": tools}


# ── Partial updates ──────────────────────────────────────────────────────────


def _update_row(
    conn,
    table: str,
    row_id: int,
    fields: dict[str, Any],
    allowed: frozenset[str],
    json_columns: frozenset[str] = frozenset(),
    has_updated_at: bool = True,
) -> dict[str, Any] | None:
    """Build a parameterized ``UPDATE ... SET`` from only the keys present in
    *fields* — the caller passes a pydantic ``model_dump(exclude_unset=True)``
    so a field the request omitted never overwrites existing data with NULL.
    Column names come only from *allowed* (a fixed whitelist per table, never
    from caller input) and are never string-interpolated with a value —
    every value travels as a parameter. Returns None if *row_id* does not
    exist (a caller-facing 404), whether or not *fields* was empty.

    *table* itself is also never caller-supplied: every call site below
    passes a literal table name, so there is no injection surface there
    either — the f-string is only ever built from constants this module
    controls.
    """
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown column(s) for {table}: {sorted(unknown)}")
    if not fields:
        return one(conn, f"SELECT * FROM {table} WHERE id = %s", (row_id,))  # noqa: S608

    set_parts = [f"{key} = %s" for key in fields]
    if has_updated_at:
        set_parts.append("updated_at = now()")
    params: list[Any] = [Json(v) if k in json_columns else v for k, v in fields.items()]
    params.append(row_id)

    row = one(
        conn,
        f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = %s RETURNING *",  # noqa: S608
        params,
    )
    conn.commit()
    return row


_ROLE_COLUMNS = frozenset({
    "name", "description", "default_ai_tool", "default_ai_model",
    "skill_refs", "instructions", "document_refs", "token_budget",
})
_ROLE_JSON_COLUMNS = frozenset({"document_refs"})


def update_role(conn, role_id: int, **fields: Any) -> dict[str, Any] | None:
    return _update_row(conn, "pm_roles", role_id, fields, _ROLE_COLUMNS, _ROLE_JSON_COLUMNS)


_MEMBER_COLUMNS = frozenset({
    "name", "member_type", "contact_info", "skill_refs", "instructions",
    "document_refs", "token_budget",
})
_MEMBER_JSON_COLUMNS = frozenset({"contact_info", "document_refs"})


def update_member(conn, member_id: int, **fields: Any) -> dict[str, Any] | None:
    return _update_row(conn, "pm_members", member_id, fields, _MEMBER_COLUMNS, _MEMBER_JSON_COLUMNS)


_PROJECT_COLUMNS = frozenset({"name", "description", "status", "token_budget"})


def update_pm_project(conn, project_id: int, **fields: Any) -> dict[str, Any] | None:
    return _update_row(conn, "pm_projects", project_id, fields, _PROJECT_COLUMNS)


_TEAM_COLUMNS = frozenset({"name", "description", "token_budget"})


def update_team(conn, team_id: int, **fields: Any) -> dict[str, Any] | None:
    # pm_teams has no updated_at column (see migrations/007_pm_ops_schema.sql).
    return _update_row(conn, "pm_teams", team_id, fields, _TEAM_COLUMNS, has_updated_at=False)


def register_existing_run(
    conn, *, pm_project_id: int, team_id: int, title: str, repo_path: str, run_id: str,
) -> dict[str, Any]:
    """Adopt a pipeline.sh run Throughline did not launch — e.g. one started
    by hand from the terminal, like razor1911-demo-tribute on 2026-08-25.
    `pid=None` means stop_task has nothing to kill; the UI hides the Stop
    button for these (Task 17).

    `run_id` becomes a path component under `repo_path` unchecked, so it is
    validated as a single path segment (no separator, no `..`, not absolute)
    before it ever reaches the filesystem — otherwise a caller could point
    log_dir anywhere on disk. The computed log_dir must already exist as a
    directory: this endpoint only *adopts* a run pipeline.sh already
    started, it never creates one.
    """
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id or Path(run_id).is_absolute():
        raise ValueError(f"run_id must be a single path component, got {run_id!r}")

    log_dir = Path(repo_path) / ".ai-pipeline" / run_id
    if not log_dir.is_dir():
        raise FileNotFoundError(f"log directory does not exist: {log_dir}")

    task = create_task(
        conn, pm_project_id=pm_project_id, team_id=team_id, title=title,
        run_id=run_id, repo_path=repo_path, log_dir=str(log_dir), pid=None,
    )
    set_task_status(conn, task["id"], "running")
    return get_task(conn, task["id"])
