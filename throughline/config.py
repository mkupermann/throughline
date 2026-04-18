"""Configuration helpers for Throughline.

Centralises the database connection config and a few environment-variable
lookups used across the CLI and scripts. Scripts in ``scripts/`` have their
own copy of ``DB_CONFIG`` for direct-execution compatibility; this module
mirrors the same logic so importers can share a single source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path
from shutil import which
from typing import Any


def get_db_config() -> dict[str, Any]:
    """Return psycopg2 connection kwargs resolved from env vars.

    Honours the standard ``PG*`` variables and falls back to the user's
    login name for ``user`` if ``PGUSER`` is not set.
    """
    return {
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


def get_claude_bin() -> str | None:
    """Locate the ``claude`` CLI binary.

    Priority: ``$CLAUDE_BIN`` env var -> ``PATH`` lookup -> ``None`` if
    nothing was found. Callers are responsible for raising a meaningful
    error when the binary is required but missing.
    """
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    return which("claude")


def get_claude_dir() -> Path:
    """Return the resolved ``~/.claude`` directory (override via ``CLAUDE_DIR``)."""
    override = os.environ.get("CLAUDE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def repo_root() -> Path:
    """Best-effort lookup of the source repository root.

    Walks up from this file looking for the marker directory ``scripts/``
    combined with ``pyproject.toml``. Falls back to the current working
    directory if no match is found — the CLI uses this to resolve the
    helper scripts when invoked from an editable install.
    """
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "scripts").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()
