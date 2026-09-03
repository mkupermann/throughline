"""Spawn ~/ai-pipeline/pipeline.sh for a resolved team.

Security note (see Global Constraints in the implementation plan): every
value that reaches the subprocess arrives through `subprocess.Popen`'s
`args` list and `env` dict, never through a shell string. The task
description the user typed is exactly one argv element — Popen with
shell=False (the default) passes it to the OS as one argument regardless of
any quote or `;` characters it contains, so there is no path from a
request body to an injected shell command, matching the same principle
throughline/api/jobs.py already states for its own fixed job registry.

Windows note: pipeline.sh is a bash script and Popen cannot exec a .sh file
via shebang on Windows (there is no kernel-level shebang interpretation
outside a POSIX exec). We always spawn it as `bash <script> ...` — bash is
present on every platform we target (Git Bash on Windows, native bash on
Linux/macOS) — rather than relying on the OS to dispatch by extension.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import psutil

from throughline.queries import pm as Q

# TOML-safe: letters, digits, and the handful of punctuation characters a
# real model identifier uses (e.g. "ollama_chat/qwen3-coder:30b",
# "gpt-4.1-mini"). Nothing here can close a `"..."` string or start a new
# TOML key/table, so a value that fails this check is rejected outright
# rather than escaped — resolved["ai_model"] is interpolated directly into
# a `"..."` TOML string below with no other quoting.
_AI_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")

PIPELINE_SCRIPT = Path(os.environ.get("AI_PIPELINE_SCRIPT_PATH", str(Path.home() / "ai-pipeline" / "pipeline.sh")))


def _resolve_bash_executable() -> str | None:
    """Find a real bash, not Windows' legacy WSL relay.

    ``shutil.which("bash")`` can return ``System32\\bash.exe`` on Windows.
    That executable delegates to WSL and cannot execute the Windows paths the
    PM launcher passes to it. Git for Windows ships the compatible bash next
    to ``cmd\\git.exe``, so prefer that installation when the PATH result is
    the relay. A non-Windows PATH result remains the normal fast path.
    """
    found = shutil.which("bash")
    if sys.platform != "win32":
        return found or "bash"

    normalised = (found or "").replace("\\", "/").casefold()
    is_wsl_relay = normalised.endswith("/windows/system32/bash.exe")
    if found and not is_wsl_relay:
        return found

    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        git_path = Path(git)
        candidates.extend(
            [
                git_path.parent.parent / "bin" / "bash.exe",
                git_path.parent.parent / "usr" / "bin" / "bash.exe",
            ]
        )

    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if not root:
            continue
        root_path = Path(root)
        candidates.append(root_path / "Git" / "bin" / "bash.exe")
        if env_name == "LOCALAPPDATA":
            candidates.append(root_path / "Programs" / "Git" / "bin" / "bash.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return None


BASH_EXECUTABLE = _resolve_bash_executable()

_STEP_TO_CONTEXT_VAR = {
    "analyst": "AI_PIPELINE_ANALYST_CONTEXT",
    "executor": "AI_PIPELINE_EXECUTOR_CONTEXT",
    "tester": "AI_PIPELINE_TESTER_CONTEXT",
}

# A role/member bound to a pm_ai_providers row (Welle D) carries
# ai_tool == "provider:<id>" instead of one of the fixed "aider"/"claude"/
# "vibe" tool names — resolve_assignment's ai_model is already the
# LiteLLM-format string (throughline/queries/pm.py's ai_catalog built it
# that way), so the only extra work here is injecting that provider's
# credentials into the spawned pipeline's environment.
_PROVIDER_TOOL_RE = re.compile(r"^provider:(\d+)$")

#: Env var each provider type's API key lands in — matching what aider/
#: LiteLLM itself reads for that provider prefix (openai/, anthropic/, ...).
#: ollama and openai_compatible are handled separately below: ollama has no
#: API key at all, and openai_compatible reuses OPENAI_API_KEY.
_PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "openai_compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "google": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _inject_provider_env(env: dict[str, str], provider: dict[str, Any]) -> None:
    """Set the credentials a pm_ai_providers row's litellm-prefixed model
    string needs to actually run, in the spawned pipeline's env dict."""
    ptype = provider["provider_type"]
    base_url = (provider.get("base_url") or "").strip()
    api_key = provider.get("api_key") or ""

    if ptype == "ollama":
        # No API key — OLLAMA_API_BASE is the only thing ollama_chat/ needs.
        env["OLLAMA_API_BASE"] = base_url or "http://127.0.0.1:11434"
        return

    env_var = _PROVIDER_API_KEY_ENV.get(ptype)
    if env_var and api_key:
        env[env_var] = api_key
    # openai/openai_compatible both point at OPENAI_API_BASE when the
    # provider overrides the default endpoint (required for
    # openai_compatible, an optional override for openai).
    if ptype in ("openai", "openai_compatible") and base_url:
        env["OPENAI_API_BASE"] = base_url


def ensure_vibe_agent_profile(resolved: dict[str, Any], profile_name: str) -> Path:
    """Write ~/.vibe/agents/<profile_name>.toml so a role/member's resolved
    AI binding is usable as a --agent for `vibe -p`. Always overwrites
    (never goes stale, no separate "is this profile up to date" check
    needed) and always read-only (write_file/edit = never) — this generator
    is only ever used for the Tester role's binding; an Executor bound to
    Vibe instead of Aider is out of scope for this iteration (spec §2 lists
    only the three-role pattern already proven on 2026-08-25).

    Mirrors ~/.vibe/agents/tester-local.toml, hand-written and verified
    working against a live `vibe -p` call earlier in this project.
    """
    ai_model = resolved["ai_model"]
    if not ai_model or not _AI_MODEL_RE.match(ai_model):
        # resolved["ai_model"] is interpolated into a bare `"..."` TOML
        # string below with no escaping — a quote or newline in it would
        # let arbitrary TOML (including reopening the write_file/edit
        # permission="never" blocks this file exists to enforce) leak into
        # the generated profile. Reject before touching the filesystem.
        raise ValueError(
            f"ai_model {ai_model!r} is not safe to write into a TOML file: "
            "expected only letters, digits, and . _ : / -"
        )

    agents_dir = Path.home() / ".vibe" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{profile_name}.toml"

    instructions = resolved.get("instructions") or ""
    lines = [
        f'display_name = "{profile_name}"',
        f'description = "Auto-generated by Throughline PM for {profile_name} — read-only, local model"',
        'safety = "safe"',
        "",
        'disabled_tools = ["exit_plan_mode"]',
        "",
        "[tools.write_file]",
        'permission = "never"',
        "",
        "[tools.edit]",
        'permission = "never"',
        "",
        f'active_model = "{profile_name}-model"',
        f'allowed_models = ["{profile_name}-model"]',
        "",
        "[[providers]]",
        f'name = "{profile_name}-provider"',
        'api_base = "http://127.0.0.1:11434/v1"',
        'backend = "generic"',
        "",
        "[[models]]",
        f'name = "{resolved["ai_model"]}"',
        f'provider = "{profile_name}-provider"',
        f'alias = "{profile_name}-model"',
        "temperature = 0.2",
    ]
    if instructions:
        # TOML `#` comments end at end-of-line — resolve_assignment joins
        # role+member instructions with "\n\n", so a single `# ...` line
        # embedding the raw (possibly multi-line) text would let everything
        # after the first newline fall through as bare top-level TOML,
        # potentially ahead of the [tools.write_file]/[tools.edit]
        # permission="never" blocks this file exists to enforce. Every
        # physical line of the instructions must get its own `#` prefix.
        comment_lines = [f"# {line}" for line in instructions.splitlines()]
        lines[3:3] = ["# Role/member instructions:", *comment_lines]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_context_file(conn, tmp_dir: Path, step: str, resolved: dict[str, Any]) -> Path | None:
    lines = []
    if resolved["instructions"]:
        lines.append(resolved["instructions"])
    if resolved["skill_refs"]:
        # resolved["skill_refs"] holds numeric skills.id values (the union of
        # role and member skill_refs) — the agent needs the human-readable
        # name, not the id, so resolve through the skills table.
        skill_names = Q.get_skill_names(conn, resolved["skill_refs"])
        if skill_names:
            lines.append("Verwende diese Skills: " + ", ".join(skill_names))
    if resolved["document_refs"]:
        lines.append("Relevante Dokumente: " + ", ".join(resolved["document_refs"]))
    if not lines:
        return None
    path = tmp_dir / f"{step}-context.md"
    path.write_text("\n\n".join(lines), encoding="utf-8")
    return path


def launch_task(conn, *, pm_project_id: int, team_id: int, title: str, repo_path: str) -> dict[str, Any]:
    if BASH_EXECUTABLE is None:
        raise RuntimeError(
            "Git Bash is required to launch Project Management tasks on Windows. "
            "Install Git for Windows, then restart Throughline."
        )

    teams = Q.get_project_teams(conn, pm_project_id)
    team = next((t for t in teams if t["id"] == team_id), None)
    if team is None:
        raise ValueError(f"Team {team_id} is not linked to project {pm_project_id}")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, r.name AS role_name FROM pm_assignments a
            JOIN pm_roles r ON r.id = a.role_id
            WHERE a.pm_project_id = %s AND a.team_id = %s
            """,
            (pm_project_id, team_id),
        )
        assignment_rows = cur.fetchall()

    # NOTE (controller ruling, overrides an earlier draft): a run_id derived
    # from pm_project_id/team_id/os.getpid()/len(assignment_rows) collides
    # across repeated launches of the same team, and pm_tasks has a
    # UNIQUE (repo_path, run_id) index — so use a random suffix instead.
    run_id = f"pm-{pm_project_id}-{team_id}-{uuid.uuid4().hex[:8]}"

    # Context files live inside the run's own log directory rather than a
    # tempfile.mkdtemp() dir — a temp dir is never cleaned up here (one
    # leaked directory per launch, unbounded), whereas
    # <repo_path>/.ai-pipeline/<run_id>/ already lives with the rest of the
    # run's artifacts, gets cleaned up along with the repo, and doubles as
    # an audit trail of exactly what each role was told.
    log_dir = Path(repo_path) / ".ai-pipeline" / run_id
    ctx_dir = log_dir / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["AI_PIPELINE_RUN_ID"] = run_id
    # pipeline.sh's child tools (vibe in particular) crash with
    # UnicodeEncodeError under codepage 850 when stdout is redirected on
    # Windows — force UTF-8 I/O for the whole subprocess tree.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    role_to_env_model = {"executor": "AI_PIPELINE_EXECUTOR_MODEL", "tester": "AI_PIPELINE_TESTER_AGENT"}

    for assignment_id, role_name in assignment_rows:
        resolved = Q.resolve_assignment(conn, assignment_id)
        role_key = role_name.strip().lower()

        ctx_var = _STEP_TO_CONTEXT_VAR.get(role_key)
        if ctx_var:
            ctx_file = _write_context_file(conn, ctx_dir, role_key, resolved)
            if ctx_file:
                env[ctx_var] = str(ctx_file)

        model_var = role_to_env_model.get(role_key)
        if model_var and resolved["ai_model"]:
            if role_key == "tester" and resolved["ai_tool"] == "vibe":
                profile_name = f"pm-{assignment_id}"
                ensure_vibe_agent_profile(resolved, profile_name)
                env[model_var] = profile_name
            else:
                env[model_var] = resolved["ai_model"]

            provider_match = _PROVIDER_TOOL_RE.match(resolved["ai_tool"] or "")
            if provider_match:
                provider = Q.get_ai_provider(conn, int(provider_match.group(1)))
                if provider is not None:
                    _inject_provider_env(env, provider)

    # Windows note (controller ruling): Popen cannot exec a .sh file via
    # shebang, so pipeline.sh is always invoked through bash explicitly —
    # this matches how the fake test script is invoked too.
    process = subprocess.Popen(
        [BASH_EXECUTABLE, str(PIPELINE_SCRIPT), title, repo_path, "300"],
        env=env,
        cwd=repo_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )

    task = Q.create_task(
        conn,
        pm_project_id=pm_project_id,
        team_id=team_id,
        title=title,
        run_id=run_id,
        repo_path=repo_path,
        log_dir=str(log_dir),
        pid=process.pid,
    )
    Q.set_task_status(conn, task["id"], "running")
    return Q.get_task(conn, task["id"])


def kill_process_tree(pid: int) -> None:
    """Kill the whole process tree rooted at *pid*, not just that one PID.

    pipeline.sh runs under Git Bash on Windows and spawns aider/vibe/claude
    as children that are not reliably reachable through a POSIX process
    group the way they would be on Linux/macOS — psutil's recursive
    `children()` walk is the portable way to find them regardless. On
    Windows, psutil's terminate() is a hard TerminateProcess (there is no
    graceful SIGTERM equivalent), so the terminate-then-kill escalation
    below still runs, it just has less room to matter there than on
    POSIX — the wait_procs timeouts are what give a well-behaved POSIX
    child a chance to exit cleanly before the kill() escalation.

    Shared by stop_task (user-requested stop) and pm_watch.poll_task (a
    tripped budget must stop burning tokens immediately, not just get
    marked budget_exceeded in the database while the run keeps going).
    """
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(children, timeout=3)
        for child in alive:
            child.kill()
        parent.terminate()
        parent.wait(timeout=3)
    except psutil.NoSuchProcess:
        pass  # already gone — stopping an already-dead process is not an error
    except psutil.TimeoutExpired:
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass


def stop_task(conn, task_id: int) -> dict[str, Any]:
    """Kill the whole process tree for task_id's recorded PID, if any."""
    task = Q.get_task(conn, task_id)
    if task["pid"] is not None:
        kill_process_tree(task["pid"])

    Q.set_task_status(conn, task_id, "stopped")
    return Q.get_task(conn, task_id)
