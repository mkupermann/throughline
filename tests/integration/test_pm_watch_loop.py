import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from throughline.jobs import pm_watch
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
    """A tripped budget must be a hard stop: the process tree is killed
    (not just flagged in the database) and an 'error' event names exactly
    which budget tripped and the numbers involved."""
    project = Q.create_pm_project(db_connection, name="WatchP3", token_budget=50000)
    team = Q.create_team(db_connection, name="WatchT3")
    log_dir = tmp_path / ".ai-pipeline" / "run3"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 51.234k sent, 0 received.", encoding="utf-8")
    # No verdict yet — run is still "in progress" from pipeline.sh's point of view.

    # A real, currently-alive process to prove poll_task actually kills it —
    # not `sleep 30` (no sleep.exe reachable via a bare CreateProcess on
    # Windows PATH), a directly-spawned dummy python process instead.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run3", repo_path=str(tmp_path), log_dir=str(log_dir), pid=proc.pid,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "budget_exceeded"

    # The process tree was actually killed, not just flagged in the DB.
    proc.wait(timeout=5)
    assert proc.returncode is not None
    assert not psutil.pid_exists(proc.pid)

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT message FROM pm_task_events WHERE task_id = %s AND event_type = 'error'",
            (task["id"],),
        )
        error_messages = [row[0] for row in cur.fetchall()]
    assert len(error_messages) == 1
    assert error_messages[0] == "project budget 50000 exceeded (used 51234)"


@pytest.mark.integration
def test_poll_task_adopted_task_with_no_pid_stays_running(db_connection, tmp_path: Path):
    """register_existing_run adopts a pipeline.sh run Throughline did not
    launch itself, so it creates the task with pid=None by design. Such a
    task must never be crash-marked just because there is no PID to check —
    only tasks Throughline itself launched get the liveness check."""
    project = Q.create_pm_project(db_connection, name="WatchP4")
    team = Q.create_team(db_connection, name="WatchT4")
    log_dir = tmp_path / ".ai-pipeline" / "run4"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    # No executor log, no verdict yet — an adopted run still in flight.

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run4", repo_path=str(tmp_path), log_dir=str(log_dir), pid=None,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "running"


@pytest.mark.integration
def test_poll_task_marks_stale_adopted_task_as_stopped(db_connection, tmp_path: Path):
    """P1.2: an adopted task (pid=None, register_existing_run) has no
    process for _pid_alive to check — the only signal that the external
    pipeline.sh run actually ended is its log_dir going quiet. 30+ minutes
    of total silence across every file in log_dir is treated as 'ended
    externally', with an error event naming the inactivity and a terminal
    'stopped' status — not left showing 'running' forever."""
    project = Q.create_pm_project(db_connection, name="StaleP")
    team = Q.create_team(db_connection, name="StaleT")
    log_dir = tmp_path / ".ai-pipeline" / "stale-run"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")

    two_hours_ago = time.time() - 2 * 60 * 60
    for f in log_dir.glob("*"):
        os.utime(f, (two_hours_ago, two_hours_ago))

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="stale-run", repo_path=str(tmp_path), log_dir=str(log_dir), pid=None,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "stopped"

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT message FROM pm_task_events WHERE task_id = %s AND event_type = 'error'",
            (task["id"],),
        )
        messages = [row[0] for row in cur.fetchall()]
    assert len(messages) == 1
    assert "extern beendet" in messages[0]
    assert "120 Minuten" in messages[0]


@pytest.mark.integration
def test_poll_task_adopted_task_with_fresh_mtimes_stays_running(db_connection, tmp_path: Path):
    """The counterpart to the staleness test above: an adopted task whose
    log_dir was just touched must not be marked stopped."""
    project = Q.create_pm_project(db_connection, name="FreshP")
    team = Q.create_team(db_connection, name="FreshT")
    log_dir = tmp_path / ".ai-pipeline" / "fresh-run"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="fresh-run", repo_path=str(tmp_path), log_dir=str(log_dir), pid=None,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "running"


@pytest.mark.integration
def test_poll_task_refreshes_executor_tokens_across_ticks(db_connection, tmp_path: Path):
    """Aider keeps appending "Tokens: ..." lines to the same executor log as
    an iteration progresses. A tick that only records tokens the first time
    the iteration's log is seen would freeze tokens_used at whatever partial
    total existed on that first tick. Each tick must refresh from the
    current log content."""
    project = Q.create_pm_project(db_connection, name="WatchP5")
    team = Q.create_team(db_connection, name="WatchT5")
    log_dir = tmp_path / ".ai-pipeline" / "run5"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 100 sent, 20 received.", encoding="utf-8")
    # No verdict yet — run is still "in progress".

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run5", repo_path=str(tmp_path), log_dir=str(log_dir), pid=os.getpid(),
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))
    first_tokens = Q.get_task(db_connection, task["id"])["tokens_used"]
    assert first_tokens == 120

    # The same iteration's log grows with a second aider turn before the
    # next tick — the executor event for iteration 1 already exists.
    (log_dir / "executor-1.log").write_text(
        "Tokens: 100 sent, 20 received.\nTokens: 50 sent, 10 received.",
        encoding="utf-8",
    )

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))
    second_tokens = Q.get_task(db_connection, task["id"])["tokens_used"]

    assert second_tokens > first_tokens
    assert second_tokens == 180


@pytest.mark.integration
def test_poll_task_marks_fail_when_pid_dead_and_latest_verdict_is_fail(db_connection, tmp_path: Path):
    """Terminal state fidelity: pipeline.sh can exit right after writing a
    FAIL verdict for its final iteration (max_iterations reached) without
    ever writing a PASS. That is a genuine 'fail', not a 'crashed' — the
    pid-dead branch must consult the latest verdict before defaulting to
    the generic crash status."""
    project = Q.create_pm_project(db_connection, name="WatchP6")
    team = Q.create_team(db_connection, name="WatchT6")
    log_dir = tmp_path / ".ai-pipeline" / "run6"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 100 sent, 20 received.", encoding="utf-8")
    (log_dir / "verdict-1.txt").write_text(
        "Nicht bestanden.\n\nVERDICT: FAIL: assertion kaputt", encoding="utf-8"
    )

    dead_pid = 999_999  # essentially guaranteed not to be a live process
    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="run6", repo_path=str(tmp_path), log_dir=str(log_dir), pid=dead_pid,
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    assert Q.get_task(db_connection, task["id"])["status"] == "fail"


@pytest.mark.integration
def test_poll_task_backfills_every_iteration_on_first_poll(db_connection, tmp_path: Path):
    """The signature backfill case (item 1 of
    docs/superpowers/specs/2026-08-26-virtual-team-ops-ui-rebuild.md): a run
    that already has several iterations on disk before Throughline ever
    polled it — e.g. an adopted run, or a watcher that missed several ticks
    — must get events for EVERY iteration on the very first poll, not just
    the latest one."""
    project = Q.create_pm_project(db_connection, name="BackfillP")
    team = Q.create_team(db_connection, name="BackfillT")
    log_dir = tmp_path / ".ai-pipeline" / "backfill-run"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 100 sent, 20 received.", encoding="utf-8")
    (log_dir / "executor-2.log").write_text("Tokens: 50 sent, 10 received.", encoding="utf-8")
    (log_dir / "executor-3.log").write_text("Tokens: 30 sent, 5 received.", encoding="utf-8")
    (log_dir / "verdict-1.txt").write_text("Nicht bestanden.\n\nVERDICT: FAIL: x", encoding="utf-8")
    (log_dir / "verdict-2.txt").write_text("Auch nicht.\n\nVERDICT: FAIL: y", encoding="utf-8")
    # No verdict-3.txt — iteration 3 is still in progress, run stays "running".

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="backfill-run", repo_path=str(tmp_path), log_dir=str(log_dir),
        pid=os.getpid(),
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT step, iteration, tokens_used FROM pm_task_events "
            "WHERE task_id = %s AND step = 'executor' ORDER BY iteration",
            (task["id"],),
        )
        executor_events = cur.fetchall()
        cur.execute(
            "SELECT iteration, message FROM pm_task_events "
            "WHERE task_id = %s AND step = 'tester' ORDER BY iteration",
            (task["id"],),
        )
        tester_events = cur.fetchall()

    assert [e[1] for e in executor_events] == [1, 2, 3]
    tokens_by_iteration = {row[1]: row[2] for row in executor_events}
    assert tokens_by_iteration == {1: 120, 2: 60, 3: 35}

    assert [e[0] for e in tester_events] == [1, 2]
    assert "VERDICT: FAIL: x" in tester_events[0][1]
    assert "VERDICT: FAIL: y" in tester_events[1][1]

    assert Q.get_task(db_connection, task["id"])["tokens_used"] == 120 + 60 + 35
    assert Q.get_task(db_connection, task["id"])["status"] == "running"


@pytest.mark.integration
def test_poll_task_backfill_does_not_duplicate_already_recorded_iterations(
    db_connection, tmp_path: Path
):
    """A second poll after the first backfilling poll must not insert
    duplicate executor/tester events for the iterations already recorded —
    only the latest iteration's executor tokens get refreshed."""
    project = Q.create_pm_project(db_connection, name="BackfillP2")
    team = Q.create_team(db_connection, name="BackfillT2")
    log_dir = tmp_path / ".ai-pipeline" / "backfill-run2"
    log_dir.mkdir(parents=True)
    (log_dir / "SPEC.md").write_text("x", encoding="utf-8")
    (log_dir / "executor-1.log").write_text("Tokens: 100 sent, 20 received.", encoding="utf-8")
    (log_dir / "executor-2.log").write_text("Tokens: 50 sent, 10 received.", encoding="utf-8")
    (log_dir / "verdict-1.txt").write_text("x\n\nVERDICT: FAIL: x", encoding="utf-8")

    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="t",
        run_id="backfill-run2", repo_path=str(tmp_path), log_dir=str(log_dir),
        pid=os.getpid(),
    )
    Q.set_task_status(db_connection, task["id"], "running")

    poll_task(db_connection, Q.get_task(db_connection, task["id"]))
    poll_task(db_connection, Q.get_task(db_connection, task["id"]))

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT step, iteration FROM pm_task_events WHERE task_id = %s "
            "AND step IN ('executor', 'tester')",
            (task["id"],),
        )
        pairs = cur.fetchall()

    assert len(pairs) == len(set(pairs))  # no duplicates
    assert sorted(pairs) == [("executor", 1), ("executor", 2), ("tester", 1)]


@pytest.mark.integration
def test_poll_all_running_isolates_task_failures(db_connection, tmp_path: Path, monkeypatch):
    """One task blowing up mid-poll (e.g. an unreadable log_dir) must not
    stop the watcher loop from polling every other running task."""
    project = Q.create_pm_project(db_connection, name="WatchP7")
    team = Q.create_team(db_connection, name="WatchT7")

    # Task A: parse_spec is made to raise for this task's log_dir specifically
    # (its own log_dir need not even exist — the mock never touches disk).
    log_dir_a = tmp_path / ".ai-pipeline" / "run-a"
    task_a = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="a",
        run_id="run-a", repo_path=str(tmp_path), log_dir=str(log_dir_a),
    )
    Q.set_task_status(db_connection, task_a["id"], "running")

    real_parse_spec = pm_watch.parse_spec

    def flaky_parse_spec(log_dir):
        if str(log_dir) == str(log_dir_a):
            raise RuntimeError("simulated unreadable log_dir")
        return real_parse_spec(log_dir)

    monkeypatch.setattr(pm_watch, "parse_spec", flaky_parse_spec)

    # Task B: healthy, must still be polled and get its event recorded even
    # though task A's poll_task call raised.
    log_dir_b = tmp_path / ".ai-pipeline" / "run-b"
    log_dir_b.mkdir(parents=True)
    (log_dir_b / "SPEC.md").write_text("## Ziel\nx", encoding="utf-8")
    task_b = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="b",
        run_id="run-b", repo_path=str(tmp_path), log_dir=str(log_dir_b),
    )
    Q.set_task_status(db_connection, task_b["id"], "running")

    pm_watch.poll_all_running(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pm_task_events WHERE task_id = %s AND step = 'analyst'",
            (task_b["id"],),
        )
        assert cur.fetchone() is not None
