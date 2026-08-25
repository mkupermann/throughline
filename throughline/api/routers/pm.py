"""Virtual Team Ops HTTP surface: catalogs, relationships, assignments,
and task launch/stop/inspect. See
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from throughline.jobs.pm_launch import launch_task, stop_task
from throughline.jobs.pm_watch import parse_verdict
from throughline.queries import pm as Q

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["pm"])

#: Maximum lines returned by the log-excerpt endpoint's `tail` query param,
#: regardless of what the caller asks for — a client requesting the whole
#: multi-thousand-line executor log should get a clamp, not an unbounded read.
_MAX_LOG_TAIL_LINES = 1000


class RoleIn(BaseModel):
    name: str
    description: str | None = None
    default_ai_tool: str | None = None
    default_ai_model: str | None = None
    skill_refs: list[int] = []
    instructions: str | None = None
    document_refs: list[str] = []
    token_budget: int | None = None


class MemberIn(BaseModel):
    name: str
    member_type: str
    contact_info: dict[str, Any] = {}
    skill_refs: list[int] = []
    instructions: str | None = None
    document_refs: list[str] = []
    token_budget: int | None = None


class TeamIn(BaseModel):
    name: str
    description: str | None = None
    token_budget: int | None = None


class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    token_budget: int | None = None


class AssignmentIn(BaseModel):
    pm_project_id: int
    team_id: int
    role_id: int
    member_id: int
    ai_tool: str | None = None
    ai_model: str | None = None


class LaunchIn(BaseModel):
    pm_project_id: int
    team_id: int
    title: str
    repo_path: str


class RegisterIn(BaseModel):
    pm_project_id: int
    team_id: int
    title: str
    repo_path: str
    run_id: str


# ── Partial-update ("PATCH") bodies ─────────────────────────────────────────
#
# Every field defaults to None/unset and `extra="forbid"`: an unrecognized
# field name in the request body is a 422 (rejected), not silently ignored —
# a typo'd field name should fail loudly rather than appear to succeed while
# updating nothing. `.model_dump(exclude_unset=True)` at the call site (not
# `exclude_none=True`) is what lets a field explicitly set to `null` clear a
# column while an omitted field leaves it untouched.


class RolePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    default_ai_tool: str | None = None
    default_ai_model: str | None = None
    skill_refs: list[int] | None = None
    instructions: str | None = None
    document_refs: list[str] | None = None
    token_budget: int | None = None


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    member_type: str | None = None
    contact_info: dict[str, Any] | None = None
    skill_refs: list[int] | None = None
    instructions: str | None = None
    document_refs: list[str] | None = None
    token_budget: int | None = None


class TeamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    token_budget: int | None = None


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    status: str | None = None
    token_budget: int | None = None


@router.post("/pm/roles")
def create_role(body: RoleIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_role(conn, **body.model_dump())


@router.get("/pm/roles")
def list_roles(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"roles": Q.list_roles(conn)}


@router.patch("/pm/roles/{role_id}")
def patch_role(
    role_id: int, body: RolePatch, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    with connection(settings) as conn:
        row = Q.update_role(conn, role_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no role with id {role_id}")
    return row


@router.post("/pm/members")
def create_member(body: MemberIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_member(conn, **body.model_dump())


@router.get("/pm/members")
def list_members(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"members": Q.list_members(conn)}


@router.patch("/pm/members/{member_id}")
def patch_member(
    member_id: int, body: MemberPatch, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    with connection(settings) as conn:
        row = Q.update_member(conn, member_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no member with id {member_id}")
    return row


@router.post("/pm/teams")
def create_team(body: TeamIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_team(conn, **body.model_dump())


@router.get("/pm/teams")
def list_teams(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"teams": Q.list_teams(conn)}


@router.patch("/pm/teams/{team_id}")
def patch_team(
    team_id: int, body: TeamPatch, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    with connection(settings) as conn:
        row = Q.update_team(conn, team_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no team with id {team_id}")
    return row


@router.post("/pm/projects")
def create_project(body: ProjectIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_pm_project(conn, **body.model_dump())


@router.get("/pm/projects")
def list_projects(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"projects": Q.list_pm_projects(conn)}


@router.patch("/pm/projects/{project_id}")
def patch_project(
    project_id: int, body: ProjectPatch, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    with connection(settings) as conn:
        row = Q.update_pm_project(conn, project_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no project with id {project_id}")
    return row


@router.get("/pm/overview")
def overview(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.overview(conn)


@router.get("/pm/skills")
def list_skills(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"skills": Q.list_skills(conn)}


@router.get("/pm/projects/{project_id}/teams")
def project_teams(project_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"teams": Q.get_project_teams(conn, project_id)}


@router.post("/pm/projects/{project_id}/teams/{team_id}")
def link_project_team(
    project_id: int, team_id: int, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    with connection(settings) as conn:
        Q.link_project_team(conn, project_id, team_id)
    return {"linked": True}


@router.post("/pm/teams/{team_id}/roles/{role_id}")
def link_team_role(
    team_id: int, role_id: int, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    with connection(settings) as conn:
        Q.link_team_role(conn, team_id, role_id)
    return {"linked": True}


@router.post("/pm/assignments")
def create_assignment(body: AssignmentIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_assignment(conn, **body.model_dump())


@router.get("/pm/projects/{project_id}/tasks")
def project_tasks(project_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"tasks": Q.list_tasks_for_project(conn, project_id)}


@router.get("/pm/tasks/{task_id}")
def get_task(task_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        try:
            return Q.get_task(conn, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pm/tasks/{task_id}/events")
def task_events(task_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM pm_task_events WHERE task_id = %s ORDER BY created_at",
                (task_id,),
            )
            columns = [c.name for c in cur.description]
            events = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    return {"events": events}


@router.get("/pm/tasks/{task_id}/iterations/{iteration}/log")
def task_iteration_log(
    task_id: int, iteration: int, tail: int = 200, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Last *tail* lines of `executor-{iteration}.log` plus the verdict text
    for that iteration, if any. `iteration` is validated as an int by
    FastAPI's path typing and the log path is built entirely from the
    task's own stored `log_dir` — no client-supplied path component ever
    reaches the filesystem."""
    tail = max(1, min(tail, _MAX_LOG_TAIL_LINES))
    with connection(settings) as conn:
        try:
            task = Q.get_task(conn, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    log_dir = Path(task["log_dir"])
    log_path = log_dir / f"executor-{iteration}.log"
    if not log_path.is_file():
        raise HTTPException(
            status_code=404, detail=f"no log for task {task_id} iteration {iteration}"
        )

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    log_tail = "\n".join(lines[-tail:])

    parsed_verdict = parse_verdict(log_dir, iteration)
    verdict = parsed_verdict[1] if parsed_verdict is not None else None

    return {"iteration": iteration, "log_tail": log_tail, "verdict": verdict}


@router.post("/pm/tasks/launch")
def launch(body: LaunchIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        try:
            return launch_task(conn, **body.model_dump())
        except ValueError as exc:
            # "Team <id> is not linked to project <id>" — the request names
            # things that do not exist together, which is a 404, not a 400:
            # the body itself is well-formed.
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pm/tasks/{task_id}/stop")
def stop(task_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        try:
            return stop_task(conn, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pm/tasks/register")
def register(body: RegisterIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        try:
            return Q.register_existing_run(conn, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except psycopg2.IntegrityError as exc:
            # A duplicate (repo_path, run_id) pair — the UNIQUE index
            # idx_pm_tasks_repo_run raises this from inside create_task's
            # INSERT. Caught here, inside the `with connection(...)` block,
            # so it never reaches connection()'s own `except psycopg2.Error`
            # — that clause treats any escaping psycopg2.Error as a broken
            # connection and turns it into a 503, which is wrong for a
            # perfectly healthy connection that just hit a constraint. The
            # transaction is aborted at this point, so it must be rolled
            # back explicitly before the connection goes back to the pool.
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail="a task for this repo_path/run_id is already registered",
            ) from exc
