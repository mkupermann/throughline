"""Status of the external launchd-backed `claude-scheduler` skill.

This is *not* the pipeline job runner (``api/jobs.py``). It reports on a
separate, optional, macOS-only skill that registers recurring Claude tasks
through launchd and keeps its config outside this repo.

It is here because the Streamlit GUI had a Scheduler page and dropping it in
the rebuild would have been a silent capability loss. It stays read-only: this
process should not be registering launchd agents on the user's behalf.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

CONFIG = Path.home() / "Library/Application Support/Claude/scheduler/config/tasks.yaml"
LOGS = Path.home() / "Library/Application Support/Claude/scheduler/logs"
LABEL_PREFIX = "com.claude.scheduler"


def _launchd_jobs() -> dict[str, dict[str, str]]:
    """Registered launchd jobs, keyed by label. Empty on any failure.

    `launchctl` does not exist off macOS and may be sandboxed; a scheduler
    panel is not worth an exception on a machine that never had the skill.
    """
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
    except Exception:
        return {}
    jobs: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        if LABEL_PREFIX not in line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            jobs[parts[2]] = {"pid": parts[0], "exit_code": parts[1]}
    return jobs


def status() -> dict[str, Any]:
    """Configured tasks and their launchd registration state."""
    if not CONFIG.is_file():
        return {
            "configured": False,
            "config_path": str(CONFIG),
            "note": (
                "The launchd-backed claude-scheduler skill is not installed on this "
                "machine. That is fine unless you want recurring Claude tasks."
            ),
            "tasks": [],
            "registered": {},
        }

    tasks: list[dict[str, Any]] = []
    error: str | None = None
    try:
        import yaml

        with open(CONFIG) as fh:
            cfg = yaml.safe_load(fh) or {}
        tasks = cfg.get("tasks", []) or []
    except Exception as exc:
        error = f"{CONFIG.name} could not be read: {exc}"

    registered = _launchd_jobs()
    for task in tasks:
        label = task.get("label") or f"{LABEL_PREFIX}.{task.get('name', '')}"
        job = registered.get(label)
        task["registered"] = job is not None
        task["exit_code"] = job.get("exit_code") if job else None

    return {
        "configured": True,
        "config_path": str(CONFIG),
        "logs_path": str(LOGS),
        "note": error,
        "tasks": tasks,
        "registered": registered,
    }
