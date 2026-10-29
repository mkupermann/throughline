"""Behavior-level checks for the wheel a user installs."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _venv_executable(env: Path, name: str) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return env / directory / f"{name}{suffix}"


def _build_wheel(tmp_path: Path) -> Path:
    wheel_dir = tmp_path / "wheel"
    env = {**os.environ, "PIP_CACHE_DIR": str(tmp_path / "pip-cache")}
    result = subprocess.run(
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
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return next(wheel_dir.glob("throughline-*.whl"))


def test_wheel_contains_runtime_jobs_for_cli_and_mcp(tmp_path: Path) -> None:
    """The installed artifact carries code used by both console surfaces.

    Removing the packaged job implementation (or reverting either runtime
    surface to ``scripts/`` imports) must fail this test.
    """
    wheel = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as artifact:
        contents = set(artifact.namelist())

    expected_jobs = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "throughline" / "jobs").glob("*.py")
        if path.name != "__init__.py"
    }
    expected_migrations = {
        path.relative_to(ROOT).as_posix() for path in (ROOT / "throughline" / "migrations").glob("*.sql")
    }

    assert expected_jobs <= contents
    assert expected_migrations <= contents
    assert "throughline/shell/backup.sh" in contents
    assert "throughline/shell/install_hooks.sh" in contents


def test_wheel_declares_process_monitor_dependency(tmp_path: Path) -> None:
    """Installing Throughline also installs the process watcher it imports."""
    wheel = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as artifact:
        metadata_name = next(name for name in artifact.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = artifact.read(metadata_name).decode("utf-8")

    assert "Requires-Dist: psutil>=5.9" in metadata


def test_clean_install_runs_throughline_entry_point_outside_checkout(tmp_path: Path) -> None:
    """A non-editable wheel runs its CLI from an unrelated working directory."""
    wheel = _build_wheel(tmp_path)
    env = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    config = (env / "pyvenv.cfg").read_text(encoding="utf-8")
    assert "include-system-site-packages = false" in config
    python = _venv_executable(env, "python")
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True)

    result = subprocess.run(
        [str(_venv_executable(env, "throughline")), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: throughline" in result.stdout

    mcp_result = subprocess.run(
        [str(_venv_executable(env, "claude-memory-mcp")), "--help"],
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


def test_source_wrapper_bootstraps_before_importing_optional_dependencies(tmp_path: Path) -> None:
    """A bare interpreter delegates to the configured dependency-bearing interpreter."""
    bare_env = tmp_path / "bare-python"
    subprocess.run([sys.executable, "-m", "venv", str(bare_env)], check=True)
    result = subprocess.run(
        [str(_venv_executable(bare_env, "python")), str(ROOT / "scripts" / "generate_embeddings.py"), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "THROUGHLINE_PYTHON": sys.executable},
    )

    assert result.returncode == 0, result.stderr
    assert "--backend" in result.stdout


@pytest.mark.skipif(
    os.name == "nt",
    reason="the packaged hook installer requires a POSIX shell; its behavior is covered by Linux CI",
)
def test_packaged_hook_uses_the_throughline_interpreter(tmp_path: Path) -> None:
    """The installed hook remains tied to the interpreter that installed it."""
    home = tmp_path / "home"
    result = subprocess.run(
        ["bash", str(ROOT / "throughline" / "shell" / "install_hooks.sh")],
        env={"HOME": str(home), "THROUGHLINE_PYTHON": sys.executable},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    settings = (home / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert f"{sys.executable} -m throughline.jobs.context_preload" in settings
