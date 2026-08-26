"""HTTP API for the Virtual Team Ops (PM) domain: catalogs, relationships,
assignments, task launch/stop/inspect. See
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402
from throughline.jobs import pm_launch  # noqa: E402

pytestmark = pytest.mark.integration

#: A tiny fake bash script standing in for pipeline.sh: it only needs to
#: create the run's log directory and exit, since the launch/stop roundtrip
#: under test never inspects the pipeline's own output.
FAKE_PIPELINE = """#!/usr/bin/env bash
set -u
mkdir -p "$2/.ai-pipeline/$AI_PIPELINE_RUN_ID"
exit 0
"""


@pytest.fixture()
def client(db_env):
    from throughline.api import deps

    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


def test_create_role_and_list_roundtrip(client):
    resp = client.post("/api/pm/roles", json={"name": "Executor", "default_ai_tool": "aider"})
    assert resp.status_code == 200
    role_id = resp.json()["id"]

    resp = client.get("/api/pm/roles")
    assert resp.status_code == 200
    assert any(r["id"] == role_id for r in resp.json()["roles"])


def test_launch_and_stop_task(client, tmp_path, monkeypatch):
    fake_script = tmp_path / "fake_pipeline.sh"
    fake_script.write_text(FAKE_PIPELINE, encoding="utf-8")
    # PIPELINE_SCRIPT is resolved from AI_PIPELINE_SCRIPT_PATH once at
    # import time (throughline/jobs/pm_launch.py), so setting the env var
    # here would have no effect on the already-imported module — patch the
    # resolved module attribute directly instead.
    monkeypatch.setattr(pm_launch, "PIPELINE_SCRIPT", fake_script)

    project = client.post("/api/pm/projects", json={"name": "ApiP"}).json()
    team = client.post("/api/pm/teams", json={"name": "ApiT"}).json()
    client.post(f"/api/pm/projects/{project['id']}/teams/{team['id']}")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)])

    resp = client.post(
        "/api/pm/tasks/launch",
        json={"pm_project_id": project["id"], "team_id": team["id"], "title": "t", "repo_path": str(repo)},
    )
    assert resp.status_code == 200
    task = resp.json()
    assert task["status"] == "running"

    resp = client.post(f"/api/pm/tasks/{task['id']}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"

    resp = client.get(f"/api/pm/projects/{project['id']}/tasks")
    assert resp.status_code == 200
    assert any(t["id"] == task["id"] for t in resp.json()["tasks"])


def test_get_task_unknown_id_is_404(client):
    resp = client.get("/api/pm/tasks/999999")
    assert resp.status_code == 404


def test_stop_unknown_task_is_404(client):
    resp = client.post("/api/pm/tasks/999999/stop")
    assert resp.status_code == 404


def test_launch_team_not_linked_is_404(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "LaunchApiP"}).json()
    team = client.post("/api/pm/teams", json={"name": "LaunchApiT"}).json()
    # deliberately NOT linked — pm_project_teams has no row for this pair

    repo = tmp_path / "repo"
    repo.mkdir()

    resp = client.post(
        "/api/pm/tasks/launch",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(repo),
        },
    )
    assert resp.status_code == 404


def test_register_rejects_path_traversal_run_id(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "RegApiP"}).json()
    team = client.post("/api/pm/teams", json={"name": "RegApiT"}).json()

    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "../../etc/passwd",
        },
    )
    assert resp.status_code == 400


def test_register_missing_log_dir_is_404(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "RegApiP2"}).json()
    team = client.post("/api/pm/teams", json={"name": "RegApiT2"}).json()

    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "never-existed",
        },
    )
    assert resp.status_code == 404


def test_register_duplicate_run_is_409(client, tmp_path):
    """Also proves the endpoint's explicit conn.rollback() after the
    IntegrityError actually clears the aborted transaction before the
    connection goes back to the pool — a follow-up request on a fresh
    connection from the same pool must still succeed."""
    project = client.post("/api/pm/projects", json={"name": "RegApiP3"}).json()
    team = client.post("/api/pm/teams", json={"name": "RegApiT3"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "dup-run"
    log_dir.mkdir(parents=True)

    body = {
        "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
        "repo_path": str(tmp_path), "run_id": "dup-run",
    }
    first = client.post("/api/pm/tasks/register", json=body)
    assert first.status_code == 200

    second = client.post("/api/pm/tasks/register", json=body)
    assert second.status_code == 409

    resp = client.get("/api/pm/roles")
    assert resp.status_code == 200


def test_log_excerpt_happy_path_with_tail_truncation(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "LogApiP"}).json()
    team = client.post("/api/pm/teams", json={"name": "LogApiT"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "log-run"
    log_dir.mkdir(parents=True)
    lines = [f"line {n}" for n in range(1, 11)]
    (log_dir / "executor-1.log").write_text("\n".join(lines), encoding="utf-8")
    (log_dir / "verdict-1.txt").write_text("Sieht gut aus.\n\nVERDICT: PASS", encoding="utf-8")

    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "log-run",
        },
    )
    assert resp.status_code == 200
    task_id = resp.json()["id"]

    resp = client.get(f"/api/pm/tasks/{task_id}/iterations/1/log", params={"tail": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["iteration"] == 1
    assert body["log_tail"] == "line 8\nline 9\nline 10"
    assert "VERDICT: PASS" in body["verdict"]


def test_log_excerpt_unknown_task_is_404(client):
    resp = client.get("/api/pm/tasks/999999/iterations/1/log")
    assert resp.status_code == 404


def test_log_excerpt_missing_iteration_file_is_404(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "LogApiP2"}).json()
    team = client.post("/api/pm/teams", json={"name": "LogApiT2"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "log-run2"
    log_dir.mkdir(parents=True)

    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "log-run2",
        },
    )
    assert resp.status_code == 200
    task_id = resp.json()["id"]

    resp = client.get(f"/api/pm/tasks/{task_id}/iterations/1/log")
    assert resp.status_code == 404


def test_log_excerpt_no_verdict_yet_is_null(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "LogApiP3"}).json()
    team = client.post("/api/pm/teams", json={"name": "LogApiT3"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "log-run3"
    log_dir.mkdir(parents=True)
    (log_dir / "executor-1.log").write_text("hello", encoding="utf-8")

    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "log-run3",
        },
    )
    task_id = resp.json()["id"]

    resp = client.get(f"/api/pm/tasks/{task_id}/iterations/1/log")
    assert resp.status_code == 200
    assert resp.json()["verdict"] is None


def test_overview_endpoint_returns_projects_and_counts(client):
    project = client.post("/api/pm/projects", json={"name": "OvApiP", "token_budget": 500}).json()
    team = client.post("/api/pm/teams", json={"name": "OvApiT"}).json()
    client.post(f"/api/pm/projects/{project['id']}/teams/{team['id']}")

    resp = client.get("/api/pm/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert "projects" in body and "counts" in body

    row = next(p for p in body["projects"] if p["id"] == project["id"])
    assert row["teams"] == 1
    assert row["token_budget"] == 500
    assert row["tasks"]["running"] == 0
    assert set(body["counts"].keys()) == {"roles", "members", "teams"}


def test_skills_endpoint_lists_seeded_skill(client, db_env):
    import psycopg2

    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO skills (name, description, path) VALUES (%s, %s, %s) RETURNING id",
                ("api-test-skill", "a skill for the picker", "/skills/api-test-skill"),
            )
            skill_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/pm/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert any(s["id"] == skill_id and s["name"] == "api-test-skill" for s in skills)


def test_skills_endpoint_deduplicates_same_name_from_multiple_paths(client, db_env):
    """scan_skills indexes the same skill name from several filesystem
    locations (project-local, user-level, plugin), producing multiple
    skills rows sharing one name. The picker must show each name once,
    preferring the most recently modified path."""
    import psycopg2

    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO skills (name, description, path, file_modified)
                VALUES (%s, %s, %s, now() - interval '1 day')
                RETURNING id
                """,
                ("dup-skill", "older path", "/skills/dup-skill-old"),
            )
            older_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO skills (name, description, path, file_modified)
                VALUES (%s, %s, %s, now())
                RETURNING id
                """,
                ("dup-skill", "newer path", "/skills/dup-skill-new"),
            )
            newer_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/pm/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    matches = [s for s in skills if s["name"] == "dup-skill"]
    assert len(matches) == 1
    assert matches[0]["id"] == newer_id
    assert matches[0]["id"] != older_id


def test_ai_catalog_reports_ollama_models_and_vibe_profiles(client, monkeypatch, tmp_path):
    """GET /pm/ai-catalog resolves real, currently-available choices —
    Ollama models (for aider), ~/.vibe/agents/*.toml profiles plus builtins
    (for vibe), and the static claude entry — rather than leaving the
    Role/Member editors' ai_tool/ai_model as unchecked free text."""
    from throughline.queries import pm as Q

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"models": [{"name": "qwen3-coder:30b"}, {"name": "devstral:latest"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(Q.urllib.request, "urlopen", lambda *a, **k: FakeResponse())

    fake_home = tmp_path / "home"
    (fake_home / ".vibe" / "agents").mkdir(parents=True)
    (fake_home / ".vibe" / "agents" / "tester-local.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    resp = client.get("/api/pm/ai-catalog")
    assert resp.status_code == 200
    tools = {t["tool"]: t for t in resp.json()["tools"]}

    assert set(tools) == {"aider", "claude", "vibe"}

    aider = tools["aider"]
    assert aider["unavailable"] is False
    assert set(aider["models"]) == {"ollama_chat/qwen3-coder:30b", "ollama_chat/devstral:latest"}

    claude = tools["claude"]
    assert claude["models"] == ["claude -p (Standard)"]
    assert claude["unavailable"] is False

    vibe = tools["vibe"]
    assert "tester-local" in vibe["models"]
    for builtin in ("ask", "plan", "accept-edits", "auto-approve"):
        assert builtin in vibe["models"]


def test_ai_catalog_reports_ollama_unavailable_when_unreachable(client, monkeypatch, tmp_path):
    from throughline.queries import pm as Q

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(Q.urllib.request, "urlopen", boom)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-vibe-here")

    resp = client.get("/api/pm/ai-catalog")
    assert resp.status_code == 200
    tools = {t["tool"]: t for t in resp.json()["tools"]}

    assert tools["aider"]["models"] == []
    assert tools["aider"]["unavailable"] is True
    # Vibe's builtins are always offered even with no ~/.vibe/agents/ dir.
    assert "ask" in tools["vibe"]["models"]


def test_patch_role_updates_instructions_budget_and_skills(client):
    role = client.post("/api/pm/roles", json={"name": "PatchRole"}).json()

    resp = client.patch(
        f"/api/pm/roles/{role['id']}",
        json={"instructions": "Sei gruendlich.", "token_budget": 12345, "skill_refs": [1, 2, 3]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["instructions"] == "Sei gruendlich."
    assert body["token_budget"] == 12345
    assert body["skill_refs"] == [1, 2, 3]
    assert body["name"] == "PatchRole"  # untouched field preserved


def test_patch_role_unknown_id_is_404(client):
    resp = client.patch("/api/pm/roles/999999", json={"name": "whatever"})
    assert resp.status_code == 404


def test_patch_role_unknown_field_is_422(client):
    role = client.post("/api/pm/roles", json={"name": "PatchRole2"}).json()
    resp = client.patch(f"/api/pm/roles/{role['id']}", json={"not_a_real_field": "x"})
    assert resp.status_code == 422


def test_patch_member_project_team_roundtrip(client):
    member = client.post("/api/pm/members", json={"name": "PatchMember", "member_type": "human"}).json()
    resp = client.patch(f"/api/pm/members/{member['id']}", json={"token_budget": 111})
    assert resp.status_code == 200
    assert resp.json()["token_budget"] == 111

    project = client.post("/api/pm/projects", json={"name": "PatchProject"}).json()
    resp = client.patch(f"/api/pm/projects/{project['id']}", json={"status": "paused"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"

    team = client.post("/api/pm/teams", json={"name": "PatchTeam"}).json()
    resp = client.patch(f"/api/pm/teams/{team['id']}", json={"token_budget": 222})
    assert resp.status_code == 200
    assert resp.json()["token_budget"] == 222


def test_list_assignments_for_project_includes_names(client):
    project = client.post("/api/pm/projects", json={"name": "AssignApiP"}).json()
    team = client.post("/api/pm/teams", json={"name": "AssignApiT"}).json()
    role = client.post("/api/pm/roles", json={"name": "AssignApiRole"}).json()
    member = client.post(
        "/api/pm/members", json={"name": "AssignApiMember", "member_type": "human"}
    ).json()

    created = client.post(
        "/api/pm/assignments",
        json={
            "pm_project_id": project["id"], "team_id": team["id"],
            "role_id": role["id"], "member_id": member["id"], "ai_tool": "aider",
        },
    ).json()

    resp = client.get(f"/api/pm/projects/{project['id']}/assignments")
    assert resp.status_code == 200
    assignments = resp.json()["assignments"]
    assert len(assignments) == 1
    row = assignments[0]
    assert row["id"] == created["id"]
    assert row["team_id"] == team["id"]
    assert row["role_id"] == role["id"]
    assert row["member_id"] == member["id"]
    assert row["ai_tool"] == "aider"
    assert row["role_name"] == "AssignApiRole"
    assert row["member_name"] == "AssignApiMember"


def test_list_assignments_for_project_empty_when_none(client):
    project = client.post("/api/pm/projects", json={"name": "AssignApiEmptyP"}).json()
    resp = client.get(f"/api/pm/projects/{project['id']}/assignments")
    assert resp.status_code == 200
    assert resp.json()["assignments"] == []


def test_delete_assignment_removes_it(client):
    project = client.post("/api/pm/projects", json={"name": "DelAssignApiP"}).json()
    team = client.post("/api/pm/teams", json={"name": "DelAssignApiT"}).json()
    role = client.post("/api/pm/roles", json={"name": "DelAssignApiRole"}).json()
    member = client.post(
        "/api/pm/members", json={"name": "DelAssignApiMember", "member_type": "human"}
    ).json()

    created = client.post(
        "/api/pm/assignments",
        json={
            "pm_project_id": project["id"], "team_id": team["id"],
            "role_id": role["id"], "member_id": member["id"],
        },
    ).json()

    resp = client.delete(f"/api/pm/assignments/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = client.get(f"/api/pm/projects/{project['id']}/assignments")
    assert resp.json()["assignments"] == []


def test_delete_assignment_unknown_id_is_404(client):
    resp = client.delete("/api/pm/assignments/999999")
    assert resp.status_code == 404


def test_delete_project_removes_it(client):
    project = client.post("/api/pm/projects", json={"name": "DelProjP"}).json()

    resp = client.delete(f"/api/pm/projects/{project['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = client.get("/api/pm/projects")
    assert not any(p["id"] == project["id"] for p in resp.json()["projects"])


def test_delete_project_unknown_id_is_404(client):
    resp = client.delete("/api/pm/projects/999999")
    assert resp.status_code == 404


def test_delete_project_with_task_is_409(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "DelProjTaskP"}).json()
    team = client.post("/api/pm/teams", json={"name": "DelProjTaskT"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "del-proj-run"
    log_dir.mkdir(parents=True)
    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "del-proj-run",
        },
    )
    assert resp.status_code == 200

    resp = client.delete(f"/api/pm/projects/{project['id']}")
    assert resp.status_code == 409

    # The connection must still be usable afterwards — proves the explicit
    # conn.rollback() actually cleared the aborted transaction.
    resp = client.get("/api/pm/roles")
    assert resp.status_code == 200


def test_delete_team_removes_it(client):
    team = client.post("/api/pm/teams", json={"name": "DelTeamP"}).json()

    resp = client.delete(f"/api/pm/teams/{team['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_delete_team_unknown_id_is_404(client):
    resp = client.delete("/api/pm/teams/999999")
    assert resp.status_code == 404


def test_delete_team_with_task_is_409(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "DelTeamTaskP"}).json()
    team = client.post("/api/pm/teams", json={"name": "DelTeamTaskT"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "del-team-run"
    log_dir.mkdir(parents=True)
    resp = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "del-team-run",
        },
    )
    assert resp.status_code == 200

    resp = client.delete(f"/api/pm/teams/{team['id']}")
    assert resp.status_code == 409

    resp = client.get("/api/pm/roles")
    assert resp.status_code == 200


def test_delete_role_removes_it(client):
    role = client.post("/api/pm/roles", json={"name": "DelRoleP"}).json()

    resp = client.delete(f"/api/pm/roles/{role['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_delete_role_unknown_id_is_404(client):
    resp = client.delete("/api/pm/roles/999999")
    assert resp.status_code == 404


def test_delete_member_removes_it(client):
    member = client.post(
        "/api/pm/members", json={"name": "DelMemberP", "member_type": "human"}
    ).json()

    resp = client.delete(f"/api/pm/members/{member['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_delete_member_unknown_id_is_404(client):
    resp = client.delete("/api/pm/members/999999")
    assert resp.status_code == 404


def test_delete_member_with_eventful_assignment_is_409(client, db_env, tmp_path):
    """pm_assignments.member_id cascades on member delete, but
    pm_task_events.assignment_id has no ON DELETE clause — a member whose
    assignment already has recorded task history must 409, not 500."""
    import psycopg2

    from throughline.queries import pm as Q

    project = client.post("/api/pm/projects", json={"name": "DelMemberEvP"}).json()
    team = client.post("/api/pm/teams", json={"name": "DelMemberEvT"}).json()
    role = client.post("/api/pm/roles", json={"name": "DelMemberEvRole"}).json()
    member = client.post(
        "/api/pm/members", json={"name": "DelMemberEvMember", "member_type": "human"}
    ).json()
    assignment = client.post(
        "/api/pm/assignments",
        json={
            "pm_project_id": project["id"], "team_id": team["id"],
            "role_id": role["id"], "member_id": member["id"],
        },
    ).json()

    conn = psycopg2.connect(**db_env)
    try:
        task = Q.create_task(
            conn, pm_project_id=project["id"], team_id=team["id"], title="t",
            run_id="del-member-ev-run", repo_path=str(tmp_path), log_dir=str(tmp_path),
        )
        Q.add_task_event(
            conn, task_id=task["id"], step="executor", event_type="log_update",
            assignment_id=assignment["id"],
        )
    finally:
        conn.close()

    resp = client.delete(f"/api/pm/members/{member['id']}")
    assert resp.status_code == 409

    resp = client.get("/api/pm/roles")
    assert resp.status_code == 200


def test_delete_task_removes_it(client, tmp_path):
    project = client.post("/api/pm/projects", json={"name": "DelTaskP"}).json()
    team = client.post("/api/pm/teams", json={"name": "DelTaskT"}).json()

    log_dir = tmp_path / ".ai-pipeline" / "del-task-run"
    log_dir.mkdir(parents=True)
    task = client.post(
        "/api/pm/tasks/register",
        json={
            "pm_project_id": project["id"], "team_id": team["id"], "title": "t",
            "repo_path": str(tmp_path), "run_id": "del-task-run",
        },
    ).json()

    resp = client.delete(f"/api/pm/tasks/{task['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = client.get(f"/api/pm/tasks/{task['id']}")
    assert resp.status_code == 404


def test_delete_task_unknown_id_is_404(client):
    resp = client.delete("/api/pm/tasks/999999")
    assert resp.status_code == 404


def test_watcher_loop_registered_and_cancelled_on_shutdown(db_env):
    """The pm watch loop (Task 13) must run for the lifetime of the app and
    be cancelled cleanly on shutdown — no live server or 10s sleep needed to
    prove that much: TestClient's context manager runs the real lifespan."""
    from throughline.api import deps

    deps.close_pool()
    app = create_app(Settings(web_dist=None))
    with TestClient(app, raise_server_exceptions=False):
        task = app.state.pm_watch_task
        assert task is not None
        assert not task.done()

    assert task.done()
    assert task.cancelled()
    deps.close_pool()


def _seed_repo_project(db_env, *, name: str, project_path: str, sessions: int = 1) -> int:
    """A `projects` row plus *sessions* `conversations` rows whose
    project_path's last segment is *name* — the generated project_name
    column (sql/schema.sql) is what joins the two tables, so the path must
    actually end in *name* for the row to show up as linked activity."""
    import psycopg2

    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO projects (name, description, status) "
                "VALUES (%s, %s, 'active') RETURNING id",
                (name, f"{name} description"),
            )
            project_id = cur.fetchone()[0]
            for _ in range(sessions):
                cur.execute(
                    "INSERT INTO conversations (session_id, project_path, started_at, message_count) "
                    "VALUES (gen_random_uuid(), %s, now(), 1)",
                    (project_path,),
                )
        conn.commit()
    finally:
        conn.close()
    return project_id


def test_repo_projects_lists_enriched_rows(client, db_env):
    project_id = _seed_repo_project(db_env, name="RepoProjA", project_path="/repo/RepoProjA")

    resp = client.get("/api/pm/repo-projects")
    assert resp.status_code == 200
    row = next(r for r in resp.json()["repo_projects"] if r["id"] == project_id)
    assert row["name"] == "RepoProjA"
    assert row["sessions"] == 1
    assert row["repo_path"] == "/repo/RepoProjA"
    assert row["last_active"] is not None
    assert row["linked_pm_project_id"] is None
    assert row["linked_pm_project_name"] is None


def test_adopt_repo_project_creates_and_links_pm_project(client, db_env):
    project_id = _seed_repo_project(db_env, name="RepoProjB", project_path="/repo/RepoProjB")

    resp = client.post(f"/api/pm/repo-projects/{project_id}/adopt")
    assert resp.status_code == 200
    pm_project = resp.json()
    assert pm_project["name"] == "RepoProjB"

    resp = client.get("/api/pm/repo-projects")
    row = next(r for r in resp.json()["repo_projects"] if r["id"] == project_id)
    assert row["linked_pm_project_id"] == pm_project["id"]
    assert row["linked_pm_project_name"] == "RepoProjB"


def test_adopt_repo_project_already_linked_is_409(client, db_env):
    """Also proves the router's job — no explicit rollback is needed here
    since RepoProjectAlreadyLinked is raised before any write, but the
    connection must still come back usable for the next request either
    way."""
    project_id = _seed_repo_project(db_env, name="RepoProjC", project_path="/repo/RepoProjC")

    resp = client.post(f"/api/pm/repo-projects/{project_id}/adopt")
    assert resp.status_code == 200

    resp = client.post(f"/api/pm/repo-projects/{project_id}/adopt")
    assert resp.status_code == 409

    resp = client.get("/api/pm/roles")
    assert resp.status_code == 200


def test_adopt_repo_project_unknown_id_is_404(client):
    resp = client.post("/api/pm/repo-projects/999999/adopt")
    assert resp.status_code == 404


def test_link_project_repo_is_idempotent_and_lists_and_unlinks(client, db_env):
    project_id = _seed_repo_project(db_env, name="RepoProjD", project_path="/repo/RepoProjD")
    pm_project = client.post("/api/pm/projects", json={"name": "LinkRepoApiP"}).json()

    resp = client.post(f"/api/pm/projects/{pm_project['id']}/repos/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["linked"] is True

    # Idempotent: linking the same pair again is still a 200, not a conflict.
    resp = client.post(f"/api/pm/projects/{pm_project['id']}/repos/{project_id}")
    assert resp.status_code == 200

    resp = client.get(f"/api/pm/projects/{pm_project['id']}/repos")
    assert resp.status_code == 200
    repos = resp.json()["repo_projects"]
    row = next(r for r in repos if r["id"] == project_id)
    assert row["linked_pm_project_id"] == pm_project["id"]
    assert row["linked_pm_project_name"] == "LinkRepoApiP"

    resp = client.delete(f"/api/pm/projects/{pm_project['id']}/repos/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = client.get(f"/api/pm/projects/{pm_project['id']}/repos")
    assert resp.json()["repo_projects"] == []


def test_unlink_project_repo_unknown_link_is_404(client, db_env):
    project_id = _seed_repo_project(db_env, name="RepoProjE", project_path="/repo/RepoProjE")
    pm_project = client.post("/api/pm/projects", json={"name": "UnlinkRepoApiP"}).json()

    resp = client.delete(f"/api/pm/projects/{pm_project['id']}/repos/{project_id}")
    assert resp.status_code == 404


def test_normalize_ollama_url_handles_bare_host_forms():
    """The Windows Ollama installer sets OLLAMA_HOST=127.0.0.1 (no scheme,
    no port) system-wide — the catalog must still reach a valid URL."""
    from throughline.queries.pm import _normalize_ollama_url

    assert _normalize_ollama_url("127.0.0.1") == "http://127.0.0.1:11434"
    assert _normalize_ollama_url("127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert _normalize_ollama_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert _normalize_ollama_url("http://myhost:9999/") == "http://myhost:9999"
    assert _normalize_ollama_url("") == "http://127.0.0.1:11434"
