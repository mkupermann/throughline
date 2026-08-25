"""HTTP API for the Virtual Team Ops (PM) domain: catalogs, relationships,
assignments, task launch/stop/inspect. See
docs/superpowers/specs/2026-08-25-virtual-team-ops-design.md.
"""

from __future__ import annotations

import subprocess

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
