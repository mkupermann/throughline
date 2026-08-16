#!/usr/bin/env python3
"""Create the private Compose settings required for a first local startup."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
from pathlib import Path

_PLACEHOLDER_PASSWORD = "replace-with-a-unique-local-secret"


def _values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


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

    for key, value in (
        ("POSTGRES_DB", "throughline"),
        ("POSTGRES_USER", "throughline"),
        ("THROUGHLINE_UID", str(os.getuid())),
        ("THROUGHLINE_GID", str(os.getgid())),
    ):
        if not values.get(key):
            lines = _upsert(lines, key, value)
            changed = True

    if changed:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if env_file.exists():
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)

    changed = initialise(args.env_file)
    state = "created or updated" if changed else "already ready"
    print(f"Compose environment {state}: {args.env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
