"""Pure parsers over a pipeline.sh run directory.

Kept dependency-free (no DB, no psycopg2) on purpose: these are exactly the
functions the watcher loop (Task 8) calls every ~10s, and the fastest way to
verify parsing logic against the real log format captured from
razor1911-demo-tribute on 2026-08-25 is a plain pytest file with tmp_path
fixtures — no database needed to test text parsing.
"""

from __future__ import annotations

import re
from pathlib import Path

import psutil

from throughline.queries import pm as Q

_VERDICT_RE = re.compile(r"^VERDICT:\s*(PASS|FAIL)(?::\s*(.*))?", re.MULTILINE)
# Aider prints e.g. "Tokens: 3.4k sent, 130 received." — the "k" suffix
# needs its own branch since int() can't parse it directly.
_TOKENS_RE = re.compile(r"Tokens:\s*([\d.]+)(k)?\s*sent,\s*([\d.]+)(k)?\s*received")


def parse_spec(log_dir: Path) -> str | None:
    spec = log_dir / "SPEC.md"
    if not spec.is_file():
        return None
    return spec.read_text(encoding="utf-8")


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
    text = verdict_file.read_text(encoding="utf-8")
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
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pm_task_events WHERE task_id = %s AND step = 'executor' "
                "AND iteration = %s LIMIT 1",
                (task_id, iteration),
            )
            already_recorded = cur.fetchone() is not None
        if not already_recorded:
            log_text = (log_dir / f"executor-{iteration}.log").read_text(
                encoding="utf-8", errors="replace"
            )
            Q.add_task_event(
                conn, task_id=task_id, step="executor", event_type="log_update",
                iteration=iteration, tokens_used=extract_aider_tokens(log_text),
            )

        verdict = parse_verdict(log_dir, iteration)
        if verdict is not None:
            status, message = verdict
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pm_task_events WHERE task_id = %s AND step = 'tester' "
                    "AND iteration = %s LIMIT 1",
                    (task_id, iteration),
                )
                already_recorded = cur.fetchone() is not None
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
    for _name, limit in budgets.items():
        if limit is not None and used >= limit:
            Q.set_task_status(conn, task_id, "budget_exceeded")
            return

    if iteration > 0:
        verdict = parse_verdict(log_dir, iteration)
        if verdict is not None and verdict[0] == "pass":
            Q.set_task_status(conn, task_id, "pass")
            return
        # A "fail" verdict alone is not terminal — pipeline.sh retries up to
        # its own max_iterations, so only a crash or a final PASS ends the
        # Throughline-side task. A FAIL verdict is recorded as an event
        # above and the task stays "running" until pipeline.sh itself exits.

    if not _pid_alive(task["pid"]):
        # pipeline.sh's own process exited without ever writing a PASS
        # verdict for the latest iteration — treat as crashed rather than
        # leaving the task "running" forever.
        Q.set_task_status(conn, task_id, "crashed")


def poll_all_running(conn) -> None:
    for task in Q.list_running_tasks(conn):
        poll_task(conn, task)
