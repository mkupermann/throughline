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
