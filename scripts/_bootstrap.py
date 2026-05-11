"""Re-exec the calling script under the project's virtualenv if present.

Throughline scripts depend on third-party packages (``psycopg2``, ``pandas``,
``streamlit`` …) that are installed into a project-local virtualenv by
``make install`` / ``pip install -e .``. The scripts' shebangs are
``#!/usr/bin/env python3``, which on most machines resolves to the *system*
interpreter — where those dependencies are not installed — causing confusing
``ModuleNotFoundError: No module named 'psycopg2'`` failures.

This helper, when called at the top of a script (before any third-party
imports), detects a ``.venv`` (or ``venv``) directory at the repo root and
re-execs the current process under that interpreter. If no venv is found, the
call is a no-op and the script continues with whatever interpreter the user
invoked.

Usage at the top of a script::

    from _bootstrap import use_venv  # noqa: E402
    use_venv()

    import psycopg2  # now resolves against the venv

Importable both as ``_bootstrap`` (when ``scripts/`` is on ``sys.path``) and as
``scripts._bootstrap`` (when the repo root is on ``sys.path``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _candidate_interpreters(repo_root: Path) -> list[Path]:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return [
        repo_root / ".venv" / bin_dir / exe,
        repo_root / "venv" / bin_dir / exe,
    ]


def use_venv() -> None:
    """Re-exec the current script under the project venv if one exists.

    Safe to call multiple times: once re-exec has happened, ``sys.executable``
    already matches a candidate and the function returns without doing
    anything.
    """
    # Find the repo root by walking up from this file.
    repo_root = Path(__file__).resolve().parent.parent

    try:
        current = Path(sys.executable).resolve()
    except OSError:
        return

    for cand in _candidate_interpreters(repo_root):
        if not cand.exists():
            continue
        try:
            if cand.resolve() == current:
                return  # already running under the venv
        except OSError:
            continue
        # Re-exec. ``os.execv`` replaces the process; nothing after this runs.
        argv = [str(cand), *sys.argv]
        os.execv(str(cand), argv)
