"""Virtual Team Ops HTTP surface: catalogs, relationships, assignments,
and task launch/stop/inspect. See
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from throughline.jobs.pm_launch import launch_task, stop_task
from throughline.queries import pm as Q

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["pm"])


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


@router.post("/pm/roles")
def create_role(body: RoleIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_role(conn, **body.model_dump())


@router.get("/pm/roles")
def list_roles(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"roles": Q.list_roles(conn)}


@router.post("/pm/members")
def create_member(body: MemberIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_member(conn, **body.model_dump())


@router.get("/pm/members")
def list_members(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"members": Q.list_members(conn)}


@router.post("/pm/teams")
def create_team(body: TeamIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_team(conn, **body.model_dump())


@router.get("/pm/teams")
def list_teams(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"teams": Q.list_teams(conn)}


@router.post("/pm/projects")
def create_project(body: ProjectIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.create_pm_project(conn, **body.model_dump())


@router.get("/pm/projects")
def list_projects(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"projects": Q.list_pm_projects(conn)}


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
        return Q.get_task(conn, task_id)


@router.get("/pm/tasks/{task_id}/events")
def task_events(task_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM pm_task_events WHERE task_id = %s ORDER BY created_at",
                (task_id,),
            )
            columns = [c.name for c in cur.description]
            events = [dict(zip(columns, row)) for row in cur.fetchall()]
    return {"events": events}


@router.post("/pm/tasks/launch")
def launch(body: LaunchIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return launch_task(conn, **body.model_dump())


@router.post("/pm/tasks/{task_id}/stop")
def stop(task_id: int, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return stop_task(conn, task_id)


@router.post("/pm/tasks/register")
def register(body: RegisterIn, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return Q.register_existing_run(conn, **body.model_dump())
