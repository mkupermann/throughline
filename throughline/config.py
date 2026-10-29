"""Configuration helpers for Throughline.

Centralises the database connection config and a few environment-variable
lookups used across the CLI and scripts. Scripts in ``scripts/`` have their
own copy of ``DB_CONFIG`` for direct-execution compatibility; this module
mirrors the same logic so importers can share a single source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def load_dotenv(path: str | os.PathLike[str] | None = None, *, override: bool = False) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file into ``os.environ``.

    Defaults to ``<repo_root>/.env``. Blank lines and ``#`` comments are
    skipped, an optional leading ``export `` is stripped, and surrounding
    single/double quotes are removed from the value. Existing environment
    variables are left untouched unless ``override=True`` — so a value
    exported in the shell (or injected by Docker) always wins over the file.

    A missing file is a no-op. Returns the mapping of keys that were applied,
    which makes the call testable and lets the CLI report what it loaded.
    """
    target = Path(path) if path is not None else repo_root() / ".env"
    if not target.is_file():
        return {}

    applied: dict[str, str] = {}
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


def get_db_config() -> dict[str, Any]:
    """Return psycopg2 connection kwargs resolved from env vars.

    Honours the standard ``PG*`` variables and falls back to the user's
    login name for ``user`` if ``PGUSER`` is not set.
    """
    return {
        "dbname": os.environ.get("PGDATABASE", "throughline"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


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
