"""Scheduler page — launchd-backed recurring Claude tasks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    page_header = app.page_header
    badge = app.badge
    status_dot = app.status_dot
    SUCCESS = app.SUCCESS
    WARNING = app.WARNING
    DANGER = app.DANGER
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED
    TEXT_FAINT = app.TEXT_FAINT

    page_header("Scheduler", "launchd-backed recurring Claude tasks")

    TASKS_YAML = Path.home() / "Library/Application Support/Claude/scheduler/config/tasks.yaml"
    LOGS_DIR = Path.home() / "Library/Application Support/Claude/scheduler/logs"

    tasks: list = []
    if not TASKS_YAML.exists():
        st.info(
            f"Scheduler not configured. The launchd-backed claude-scheduler skill "
            f"reads `{TASKS_YAML}` to register recurring tasks; that file does "
            f"not exist on this machine yet, which is fine if you have not "
            f"installed the skill. To enable scheduling, install the skill or "
            f"create the file with `tasks: []` and add entries through this page."
        )
    else:
        try:
            with open(TASKS_YAML) as f:
                tasks_cfg = yaml.safe_load(f) or {}
            tasks = tasks_cfg.get("tasks", []) or []
        except (yaml.YAMLError, OSError, PermissionError) as e:
            st.error(f"tasks.yaml could not be read: {e}")

    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        launchd_jobs = {}
        for line in result.stdout.split("\n"):
            if "com.claude.scheduler" in line:
                parts = line.split()
                if len(parts) >= 3:
                    launchd_jobs[parts[2]] = {"pid": parts[0], "exit_code": parts[1]}
    except Exception:
        launchd_jobs = {}

    tm1, tm2 = st.columns(2)
    tm1.metric("Configured tasks", len(tasks))
    tm2.metric("launchd registered", len(launchd_jobs))

    st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
    ac1, ac2, _ = st.columns([2, 2, 8])
    with ac1:
        if st.button("Reinstall launchd", use_container_width=True):
            try:
                r = subprocess.run(
                    [str(Path.home() / ".claude/skills/claude-scheduler/scripts/install-schedule.sh"), "install"],
                    capture_output=True, text=True, timeout=30,
                )
                st.code(r.stdout + r.stderr)
            except Exception as e:
                st.error(str(e))
    with ac2:
        if st.button("Uninstall all", use_container_width=True):
            try:
                r = subprocess.run(
                    [str(Path.home() / ".claude/skills/claude-scheduler/scripts/install-schedule.sh"), "uninstall"],
                    capture_output=True, text=True, timeout=30,
                )
                st.code(r.stdout + r.stderr)
            except Exception as e:
                st.error(str(e))

    st.markdown('<hr/>', unsafe_allow_html=True)

    for task in tasks:
        name = task.get("name", "?")
        schedule = task.get("schedule", "?")
        enabled = task.get("enabled", True)
        description = task.get("description", "")
        label = f"com.claude.scheduler.{name.lower().replace(' ', '-')}"
        is_loaded = label in launchd_jobs

        if enabled and is_loaded:
            dot_color, state_text = SUCCESS, "Active"
        elif not enabled:
            dot_color, state_text = WARNING, "Paused"
        else:
            dot_color, state_text = DANGER, "Not loaded"

        pid = launchd_jobs.get(label, {}).get("pid", "—")

        with st.container(border=True):
            c1, c2, c3 = st.columns([6, 2, 2])
            with c1:
                st.markdown(
                    f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
                        {status_dot(dot_color)}
                        <span style="font-size:15px;font-weight:600;color:{TEXT};">{name}</span>
                        {badge(state_text, dot_color)}
                    </div>
                    <div style="font-size:12.5px;color:{TEXT_MUTED};margin-bottom:8px;">{description}</div>
                    <div style="font-size:11px;color:{TEXT_FAINT};font-family:'SF Mono',monospace;">
                        cron {schedule} · PID {pid}
                    </div>""",
                    unsafe_allow_html=True,
                )
            with c2:
                if st.button("Run now", key=f"run_{name}", use_container_width=True):
                    try:
                        subprocess.run(
                            [str(Path.home() / ".claude/skills/claude-scheduler/scripts/run-task.sh"), name],
                            capture_output=True, text=True, timeout=5,
                        )
                        st.toast(f"Started: {name}")
                    except Exception as e:
                        st.error(str(e))
            with c3:
                if enabled:
                    if st.button("Pause", key=f"off_{name}", use_container_width=True):
                        for t in tasks:
                            if t.get("name") == name:
                                t["enabled"] = False
                        TASKS_YAML.parent.mkdir(parents=True, exist_ok=True)
                        with open(TASKS_YAML, "w") as f:
                            yaml.safe_dump({"tasks": tasks}, f, allow_unicode=True)
                        st.rerun()
                else:
                    if st.button("Enable", key=f"on_{name}", use_container_width=True):
                        for t in tasks:
                            if t.get("name") == name:
                                t["enabled"] = True
                        TASKS_YAML.parent.mkdir(parents=True, exist_ok=True)
                        with open(TASKS_YAML, "w") as f:
                            yaml.safe_dump({"tasks": tasks}, f, allow_unicode=True)
                        st.rerun()

            with st.expander("Prompt"):
                st.code(task.get("prompt", ""), language="markdown")

            log_base = LOGS_DIR / f"{name.lower().replace(' ', '-')}"
            stdout_log = Path(str(log_base) + "-stdout.log")
            stderr_log = Path(str(log_base) + "-stderr.log")
            if stdout_log.exists() or stderr_log.exists():
                with st.expander("Recent logs"):
                    if stdout_log.exists():
                        st.markdown('<div class="kai-section-label">stdout</div>', unsafe_allow_html=True)
                        st.code(stdout_log.read_text()[-2000:] or "(empty)")
                    if stderr_log.exists():
                        st.markdown('<div class="kai-section-label">stderr</div>', unsafe_allow_html=True)
                        st.code(stderr_log.read_text()[-2000:] or "(empty)")
