import os
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from throughline.jobs.pm_launch import launch_task
from throughline.queries import pm as Q


FAKE_PIPELINE = """#!/usr/bin/env bash
set -u
echo "fake pipeline ran"
echo "TASK=$1"
echo "REPO=$2"
echo "RUN_ID=$AI_PIPELINE_RUN_ID"
echo "EXECUTOR_MODEL=$AI_PIPELINE_EXECUTOR_MODEL"
mkdir -p "$2/.ai-pipeline/$AI_PIPELINE_RUN_ID"
echo "## Ziel\\nfake spec" > "$2/.ai-pipeline/$AI_PIPELINE_RUN_ID/SPEC.md"
sleep 0.2
"""


@pytest.mark.integration
def test_launch_task_spawns_process_and_creates_task_row(db_connection, tmp_path, monkeypatch):
    fake_script = tmp_path / "fake_pipeline.sh"
    fake_script.write_text(FAKE_PIPELINE, encoding="utf-8")
    fake_script.chmod(fake_script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AI_PIPELINE_SCRIPT_PATH", str(fake_script))

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)])

    project = Q.create_pm_project(db_connection, name="LaunchP")
    team = Q.create_team(db_connection, name="LaunchT")
    role = Q.create_role(db_connection, name="Executor", default_ai_tool="aider", default_ai_model="qwen3-coder:30b")
    member = Q.create_member(db_connection, name="Agent A", member_type="agent")
    Q.link_project_team(db_connection, project["id"], team["id"])
    Q.link_team_role(db_connection, team["id"], role["id"])
    Q.create_assignment(
        db_connection, pm_project_id=project["id"], team_id=team["id"],
        role_id=role["id"], member_id=member["id"],
    )

    task = launch_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"],
        title="fake run", repo_path=str(repo),
    )

    assert task["status"] == "running"
    assert task["pid"] is not None
    time.sleep(0.5)  # let the fake script finish writing its SPEC.md

    log_dir = Path(task["log_dir"])
    assert (log_dir / "SPEC.md").is_file()


def test_ensure_vibe_agent_profile_writes_readonly_toml(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".vibe" / "agents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    from throughline.jobs.pm_launch import ensure_vibe_agent_profile

    resolved = {
        "ai_model": "devstral", "instructions": "Sei streng.",
        "skill_refs": [], "document_refs": [],
    }
    path = ensure_vibe_agent_profile(resolved, "pm-7-3")

    assert path == fake_home / ".vibe" / "agents" / "pm-7-3.toml"
    content = path.read_text(encoding="utf-8")
    assert 'permission = "never"' in content
    assert "devstral" in content


def test_ensure_vibe_agent_profile_comment_escapes_multiline_instructions(tmp_path, monkeypatch):
    # resolve_assignment joins role+member instructions with "\n\n" — this
    # is the NORMAL case, not an edge case. A raw multi-line value embedded
    # in a single `#` comment would leak everything after the first newline
    # as bare top-level TOML, potentially ahead of the permission="never"
    # blocks this file exists to enforce.
    fake_home = tmp_path / "home"
    (fake_home / ".vibe" / "agents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    from throughline.jobs.pm_launch import ensure_vibe_agent_profile

    instructions = "Sei streng.\n\nPruefe alles doppelt."
    resolved = {
        "ai_model": "devstral", "instructions": instructions,
        "skill_refs": [], "document_refs": [],
    }
    path = ensure_vibe_agent_profile(resolved, "pm-multiline")
    content = path.read_text(encoding="utf-8")

    # (a) the file parses as valid TOML at all
    parsed = tomllib.loads(content)

    # (b) the permission="never" blocks survive parsing intact
    assert parsed["tools"]["write_file"]["permission"] == "never"
    assert parsed["tools"]["edit"]["permission"] == "never"

    # (c) none of the instructions text appears outside a comment line —
    # every physical line of `content` that contains a fragment of the
    # instructions must start (after stripping leading whitespace) with "#"
    for raw_line in instructions.splitlines():
        if not raw_line:
            continue
        for content_line in content.splitlines():
            if raw_line in content_line:
                assert content_line.lstrip().startswith("#"), (
                    f"instructions text leaked outside a comment: {content_line!r}"
                )


@pytest.mark.integration
def test_stop_task_kills_process_and_updates_status(db_connection, tmp_path, monkeypatch):
    # A real long-running child process to kill: a Python process sleeping
    # for 30s via Popen directly (not through pipeline.sh) is enough to
    # exercise the tree-kill logic. Not `sleep 30` — there is no sleep.exe
    # reachable via a bare CreateProcess on Windows PATH.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    project = Q.create_pm_project(db_connection, name="StopP")
    team = Q.create_team(db_connection, name="StopT")
    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="stop-run", repo_path=str(tmp_path), log_dir=str(tmp_path),
        pid=proc.pid,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    from throughline.jobs.pm_launch import stop_task
    stopped = stop_task(db_connection, task["id"])

    assert stopped["status"] == "stopped"
    proc.wait(timeout=5)
    assert proc.returncode is not None
