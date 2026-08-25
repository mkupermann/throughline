import os
from pathlib import Path

import pytest

from throughline.jobs.pm_watch import poll_task
from throughline.queries import pm as Q


@pytest.mark.integration
def test_poll_task_records_verdict_and_marks_pass(db_connection, tmp_path: Path):
    project = Q.create_pm_project(db_connection, name="WatchP")
    team = Q.create_team(db_connection, name="WatchT")
    log_dir = tmp_path / ".ai-pipeline" / "run1"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("## Ziel\nx", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 100 sent, 20 received.", encoding="utf-8")
    (log_dir / "verdict-1.txt").write_text("Sieht gut aus.\n\nVERDICT: PASS", encoding="utf-8")

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run1", repo_path=str(tmp_path), log_dir=str(log_dir),
        pid=os.getpid(),  # a real, currently-alive PID so liveness check passes
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    updated = Q.get_task(db_connection, task["id"])
    assert updated["status"] == "pass"
    assert updated["tokens_used"] == 120


@pytest.mark.integration
def test_poll_task_marks_crashed_when_pid_dead_and_no_verdict(db_connection, tmp_path: Path):
    project = Q.create_pm_project(db_connection, name="WatchP2")
    team = Q.create_team(db_connection, name="WatchT2")
    log_dir = tmp_path / ".ai-pipeline" / "run2"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")

    # A PID essentially guaranteed not to be a live process.
    dead_pid = 999_999
    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run2", repo_path=str(tmp_path), log_dir=str(log_dir), pid=dead_pid,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "crashed"


@pytest.mark.integration
def test_poll_task_stops_on_budget_exceeded(db_connection, tmp_path: Path):
    project = Q.create_pm_project(db_connection, name="WatchP3", token_budget=50)
    team = Q.create_team(db_connection, name="WatchT3")
    log_dir = tmp_path / ".ai-pipeline" / "run3"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 100 sent, 20 received.", encoding="utf-8")
    # No verdict yet — run is still "in progress" from pipeline.sh's point of view.

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run3", repo_path=str(tmp_path), log_dir=str(log_dir), pid=os.getpid(),
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "budget_exceeded"
