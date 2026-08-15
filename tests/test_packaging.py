"""Behavior-level checks for the wheel a user installs."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _build_wheel(tmp_path: Path) -> Path:
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return next(wheel_dir.glob("throughline-*.whl"))


def test_wheel_contains_runtime_jobs_for_cli_and_mcp(tmp_path: Path) -> None:
    """The installed artifact carries code used by both console surfaces.

    Removing the packaged job implementation (or reverting either runtime
    surface to ``scripts/`` imports) must fail this test.
    """
    wheel = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as artifact:
        contents = set(artifact.namelist())

    assert "throughline/jobs/generate_embeddings.py" in contents
    assert "throughline/jobs/forget.py" in contents
    assert "throughline/jobs/graph_query.py" in contents
    assert "throughline/shell/backup.sh" in contents
    assert "throughline/shell/install_hooks.sh" in contents


def test_clean_install_runs_throughline_entry_point_outside_checkout(tmp_path: Path) -> None:
    """A non-editable wheel runs its CLI from an unrelated working directory."""
    wheel = _build_wheel(tmp_path)
    env = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(env)], check=True)
    python = env / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True)

    result = subprocess.run(
        [str(env / "bin" / "throughline"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: throughline" in result.stdout

    mcp_result = subprocess.run(
        [str(env / "bin" / "claude-memory-mcp"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert mcp_result.returncode == 0, mcp_result.stderr
    assert "Memory MCP server" in mcp_result.stdout


def test_source_script_wrapper_remains_directly_executable() -> None:
    """The documented source-checkout script path remains a compatibility surface."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_embeddings.py"), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--backend" in result.stdout
