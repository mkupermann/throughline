#!/usr/bin/env python3
"""Create the private Compose settings required for a first local startup."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path

_PLACEHOLDER_PASSWORD = "replace-with-a-unique-local-secret"


def _values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def cline_tasks_dir(placeholder: Path, candidates: list[Path] | None = None) -> Path:
    """A Cline tasks directory that exists, so Docker has something to mount.

    Cline stores under the editor's globalStorage, which differs per platform.
    Compose needs a host path that is present: a bind mount whose source does
    not exist fails the whole `up`, so a machine without Cline is given an
    empty placeholder rather than a path from someone else's operating system.
    """
    if candidates is None:
        try:
            from throughline.adapters.cline import _candidate_task_roots

            candidates = list(_candidate_task_roots())
        except Exception:
            # Runs before the package is installed. The placeholder is correct
            # either way, and the next run picks up the real directory.
            candidates = []
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    placeholder.mkdir(parents=True, exist_ok=True)
    return placeholder


def _upsert(lines: list[str], key: str, value: str) -> list[str]:
    replacement = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.partition("=")[0].strip() == key:
            lines[index] = replacement
            return lines
    lines.append(replacement)
    return lines


def initialise(env_file: Path) -> bool:
    """Add missing Compose secret/identity values without replacing user config."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    values = _values(lines)
    changed = False

    password = values.get("POSTGRES_PASSWORD", "")
    if not password or password == _PLACEHOLDER_PASSWORD:
        lines = _upsert(lines, "POSTGRES_PASSWORD", secrets.token_urlsafe(32))
        changed = True

    for key, value in (("POSTGRES_DB", "throughline"), ("POSTGRES_USER", "throughline")):
        if not values.get(key):
            lines = _upsert(lines, key, value)
            changed = True

    # Host identity is intentionally refreshed, unlike the database secret:
    # a checkout can move from macOS to Linux or between local user accounts.
    # os.getuid is POSIX-only. Windows has no numeric uid to map, and the
    # containers are Linux either way, so the conventional first non-root id
    # is the right answer there — without it this raised AttributeError before
    # writing a single line, and `make docker-up` depends on it.
    uid = str(os.getuid()) if hasattr(os, "getuid") else "1000"
    gid = str(os.getgid()) if hasattr(os, "getgid") else "1000"
    cline = cline_tasks_dir(Path.cwd() / ".cline-tasks")
    for key, value in (
        ("THROUGHLINE_UID", uid),
        ("THROUGHLINE_GID", gid),
        ("THROUGHLINE_CLINE_DIR", str(cline)),
    ):
        if values.get(key) != value:
            lines = _upsert(lines, key, value)
            changed = True

    if changed:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if env_file.exists():
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return changed


def check_docker_source_access() -> str | None:
    """Reject known incompatible source mounts without touching host permissions."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .SecurityOptions}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            return "Docker is not reachable. Start Docker and select the intended context, then retry."
        options = json.loads(result.stdout)
        if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
            raise ValueError("Unexpected Docker security options")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return "Cannot inspect Docker security options. Check `docker info` before starting Compose."
    if sys.platform.startswith("linux"):
        names = {item.split(",", 1)[0] for item in options}
        if names & {"name=rootless", "name=userns"}:
            return (
                "This Docker daemon uses rootless/user-namespace mapping. Matching container UID/GID "
                "does not grant access to private host transcripts (0600). Use native Throughline "
                "ingestion with PostgreSQL, or a separately configured rootful Docker context. "
                "See docs/DEPLOYMENT.md#private-source-mount-compatibility."
            )
        if "name=selinux" in names:
            return (
                "This Docker daemon has SELinux label support enabled. The default source mounts do not "
                "relabel private tool directories. Use native ingestion or an administrator-reviewed "
                "mount policy. See docs/DEPLOYMENT.md#private-source-mount-compatibility."
            )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--check-docker", action="store_true", help="Check private source mount compatibility before writing settings"
    )
    args = parser.parse_args(argv)
    if args.check_docker:
        problem = check_docker_source_access()
        if problem:
            print(f"Compose preflight: {problem}", file=sys.stderr)
            return 2

    changed = initialise(args.env_file)
    state = "created or updated" if changed else "already ready"
    print(f"Compose environment {state}: {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
