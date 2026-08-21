#!/usr/bin/env python3
"""Refuse to commit anything that is not part of this project.

This repository is public. An agent run with its working directory in the
checkout created a folder here for an unrelated project — untracked, not
ignored, and one `git add -A` away from being published. None of the other
pre-commit hooks would have caught it: they check formatting, file size and
private keys, not whether a file belongs here at all.

The rule is deliberately crude, because a subtle one would not be trusted:
every staged path must start with a top-level entry this project already has.
Adding a genuinely new top-level file or directory means adding it to
``ALLOWED`` in the same commit, which is exactly the moment to notice.

Usage (pre-commit passes the staged files as arguments)::

    python3 scripts/check_toplevel.py path [path ...]
"""

from __future__ import annotations

import sys

#: Every top-level entry this project tracks. Sorted so a diff that adds one
#: is a single readable line.
ALLOWED: frozenset[str] = frozenset(
    {
        ".dockerignore",
        ".env.example",
        ".gitattributes",
        ".github",
        ".gitignore",
        ".markdownlint-cli2.jsonc",
        ".graphifyignore",
        ".pre-commit-config.yaml",
        ".superpowers",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "Dockerfile",
        "LICENSE",
        "Makefile",
        "README.md",
        "SECURITY.md",
        "config.example.yaml",
        "docker-compose.yml",
        "docs",
        "evals",
        "examples",
        "launchd",
        "memory_mcp",
        "pyproject.toml",
        "pytest.ini",
        "requirements-dev.txt",
        "requirements.txt",
        "scripts",
        "skill",
        "sql",
        "systemd",
        "tests",
        "throughline",
        "web",
        "windows",
    }
)


def stray_entries(paths: list[str]) -> list[str]:
    """Top-level entries among *paths* that this project does not own.

    Reported once each, however many files sit under them: twenty files in one
    stray folder is one mistake, and twenty lines of output is how a useful
    message becomes a wall nobody reads.
    """
    seen: dict[str, None] = {}
    for raw in paths:
        path = (raw or "").strip().replace("\\", "/")
        # A literal "./" prefix, not a set of characters: lstrip("./") also
        # eats the leading dot of ".github", which turned every dotfile this
        # project owns into a stray.
        while path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        top = path.split("/", 1)[0]
        if top and top not in ALLOWED:
            seen.setdefault(top, None)
    return list(seen)


def main(argv: list[str] | None = None) -> int:
    strays = stray_entries(list(argv if argv is not None else sys.argv[1:]))
    if not strays:
        return 0
    print("Refusing to commit entries that are not part of this project:", file=sys.stderr)
    for entry in strays:
        print(f"  {entry}", file=sys.stderr)
    print(
        "\nThis repository is public. Move it out of the checkout, or add it to\n"
        "ALLOWED in scripts/check_toplevel.py if it genuinely belongs here.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
