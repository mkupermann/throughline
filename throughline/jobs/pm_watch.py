"""Parsers over a pipeline.sh run directory, plus the watcher loop itself.

The parsing functions below (parse_spec, latest_iteration, parse_verdict,
extract_aider_tokens) are kept dependency-free (no DB, no psycopg2) on
purpose: the fastest way to verify parsing logic against the real log format
captured from razor1911-demo-tribute on 2026-08-25 is a plain pytest file
with tmp_path fixtures — no database needed to test text parsing. poll_task
and poll_all_running, which the watcher loop calls every ~10s, are NOT
dependency-free — they need a live DB connection (via throughline.queries.pm)
and psutil for process-liveness checks.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import psutil

from throughline.jobs.pm_launch import kill_process_tree
from throughline.queries import pm as Q

log = logging.getLogger("throughline.pm_watch")

_VERDICT_RE = re.compile(r"^VERDICT:\s*(PASS|FAIL)(?::\s*(.*))?", re.MULTILINE)
# Aider prints e.g. "Tokens: 3.4k sent, 130 received." — the "k" suffix
# needs its own branch since int() can't parse it directly.
_TOKENS_RE = re.compile(r"Tokens:\s*([\d.]+)(k)?\s*sent,\s*([\d.]+)(k)?\s*received")


def parse_spec(log_dir: Path) -> str | None:
    spec = log_dir / "SPEC.md"
    if not spec.is_file():
        return None
    return spec.read_text(encoding="utf-8", errors="replace")


def latest_iteration(log_dir: Path) -> int:
    highest = 0
    for f in log_dir.glob("executor-*.log"):
        try:
            n = int(f.stem.split("-")[1])
        except (IndexError, ValueError):
            continue
        highest = max(highest, n)
    return highest


def parse_verdict(log_dir: Path, iteration: int) -> tuple[str, str] | None:
    verdict_file = log_dir / f"verdict-{iteration}.txt"
    if not verdict_file.is_file():
        return None
    text = verdict_file.read_text(encoding="utf-8", errors="replace")
    match = _VERDICT_RE.search(text)
    if not match:
        return None
    status = "pass" if match.group(1) == "PASS" else "fail"
    return status, text.strip()


def _to_int(value: str, suffix: str | None) -> int:
    n = float(value)
    if suffix == "k":
        n *= 1000
    return int(n)


def extract_aider_tokens(log_text: str) -> int:
    total = 0
    for sent_val, sent_k, recv_val, recv_k in _TOKENS_RE.findall(log_text):
        total += _to_int(sent_val, sent_k) + _to_int(recv_val, recv_k)
    return total


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def poll_task(conn, task: dict) -> None:
    """One watcher tick for one running task. Idempotent-ish: re-reading the
    same log state twice inserts duplicate 'log_update' events, which is
    acceptable — the drill-down is a log, not a state machine that needs
    exactly-once semantics, and `tokens_used` is always a fresh SUM, not an
    increment, so double-counting from a duplicate event is the actual bug
    to avoid: this function only inserts a 'log_update' when the iteration
    number has advanced since the last insert for this task, never on every
    tick blindly."""
    log_dir = Path(task["log_dir"])
    task_id = task["id"]

    spec = parse_spec(log_dir)
    if spec is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pm_task_events WHERE task_id = %s AND step = 'analyst' LIMIT 1",
                (task_id,),
            )
            already_recorded = cur.fetchone() is not None
        if not already_recorded:
            Q.add_task_event(conn, task_id=task_id, step="analyst", event_type="started", message=spec)

    iteration = latest_iteration(log_dir)
    if iteration > 0:
        # One combined query for every (step, iteration) pair already
        # recorded for this task, rather than a SELECT per iteration below —
        # both the backfill loop and the latest-iteration checks that follow
        # consult this same set.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step, iteration FROM pm_task_events "
                "WHERE task_id = %s AND iteration IS NOT NULL",
                (task_id,),
            )
            existing_pairs = {(row[0], row[1]) for row in cur.fetchall()}

        # Backfill every earlier iteration not yet recorded — this is what
        # makes an adopted run's first poll (register_existing_run, or any
        # tick that catches up after being down) retroactively record the
        # whole history on disk, not just the latest iteration. The latest
        # iteration itself keeps the insert-or-refresh treatment below.
        for n in range(1, iteration):
            executor_log = log_dir / f"executor-{n}.log"
            if ("executor", n) not in existing_pairs and executor_log.is_file():
                n_tokens = extract_aider_tokens(
                    executor_log.read_text(encoding="utf-8", errors="replace")
                )
                Q.add_task_event(
                    conn, task_id=task_id, step="executor", event_type="log_update",
                    iteration=n, tokens_used=n_tokens,
                )
            n_verdict = parse_verdict(log_dir, n)
            if n_verdict is not None and ("tester", n) not in existing_pairs:
                _, n_message = n_verdict
                Q.add_task_event(
                    conn, task_id=task_id, step="tester", event_type="verdict",
                    iteration=n, message=n_message,
                )

        already_recorded = ("executor", iteration) in existing_pairs
        log_text = (log_dir / f"executor-{iteration}.log").read_text(
            encoding="utf-8", errors="replace"
        )
        tokens = extract_aider_tokens(log_text)
        if not already_recorded:
            Q.add_task_event(
                conn, task_id=task_id, step="executor", event_type="log_update",
                iteration=iteration, tokens_used=tokens,
            )
        else:
            # Aider keeps appending "Tokens: ..." lines to the same log as
            # the iteration progresses — re-reading and refreshing here
            # (rather than only on first sight of the iteration) is what
            # keeps tokens_used from freezing at whatever the log happened
            # to contain on the very first tick after the file appeared.
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pm_task_events SET tokens_used = %s "
                    "WHERE task_id = %s AND step = 'executor' AND iteration = %s",
                    (tokens, task_id, iteration),
                )
            conn.commit()

        verdict = parse_verdict(log_dir, iteration)
        if verdict is not None:
            status, message = verdict
            already_recorded = ("tester", iteration) in existing_pairs
            if not already_recorded:
                Q.add_task_event(
                    conn, task_id=task_id, step="tester", event_type="verdict",
                    iteration=iteration, message=message,
                )

    Q.recompute_task_tokens(conn, task_id)

    fresh = Q.get_task(conn, task_id)
    if fresh["status"] != "running":
        return  # a check below already concluded this task on an earlier tick

    budgets = Q.budgets_for_task(conn, task_id)
    used = fresh["tokens_used"]
    for name, limit in budgets.items():
        if limit is not None and used >= limit:
            # Hard stop: a tripped budget must stop the run from burning
            # more tokens right now, not just get flagged in the database
            # while pipeline.sh keeps going in the background. Tasks
            # Throughline did not launch itself (pid=None, adopted via
            # register_existing_run) have nothing of ours to kill.
            if task["pid"] is not None:
                kill_process_tree(task["pid"])
            Q.add_task_event(
                conn, task_id=task_id, step="executor", event_type="error",
                message=f"{name.replace('_', ' ')} {limit} exceeded (used {used})",
            )
            Q.set_task_status(conn, task_id, "budget_exceeded")
            return

    latest_verdict = None
    if iteration > 0:
        latest_verdict = parse_verdict(log_dir, iteration)
        if latest_verdict is not None and latest_verdict[0] == "pass":
            Q.set_task_status(conn, task_id, "pass")
            return
        # A "fail" verdict alone is not terminal — pipeline.sh retries up to
        # its own max_iterations, so only a crash or a final PASS ends the
        # Throughline-side task. A FAIL verdict is recorded as an event
        # above and the task stays "running" until pipeline.sh itself exits.

    if task["pid"] is not None and not _pid_alive(task["pid"]):
        # pipeline.sh's own process exited without ever writing a PASS
        # verdict for the latest iteration. If the last thing recorded was a
        # FAIL verdict, that is the true terminal state — report it as such
        # rather than the generic "crashed", which should mean pipeline.sh
        # itself died mid-run with no verdict at all. Tasks Throughline did
        # not launch itself (register_existing_run adopts a run with
        # pid=None, by design, since there is no Throughline-owned process
        # to track) skip this check entirely rather than being crash-marked
        # on the very first tick.
        if latest_verdict is not None and latest_verdict[0] == "fail":
            Q.set_task_status(conn, task_id, "fail")
        else:
            Q.set_task_status(conn, task_id, "crashed")


def poll_all_running(conn) -> None:
    """Poll every running task, isolating one task's failure from the rest.

    A single task with a broken log_dir (or any other unexpected error)
    must not stall the watcher loop for every other running task — log and
    move on to the next one instead of letting the exception propagate out
    of the loop.
    """
    for task in Q.list_running_tasks(conn):
        try:
            poll_task(conn, task)
        except Exception:
            log.exception("poll_task failed for task_id=%s", task.get("id"))
            try:
                conn.rollback()
            except Exception:
                pass
