#!/usr/bin/env python3
"""Refuse commits whose message carries a co-author or an AI-session trailer.

This repository is public and was rebuilt once to drop such trailers from its
history. An agent session later pushed commits with ``Co-Authored-By:`` and
``Claude-Session:`` lines straight to ``main``. Branch protection now forces
pull requests; this check fails a pull request (or a push) that would bring
the trailers back.

Usage::

    python3 scripts/check_commit_messages.py <git revision range>
"""

from __future__ import annotations

import re
import subprocess
import sys

#: Lines that must not appear in a commit message (case-insensitive).
FORBIDDEN = re.compile(
    r"^(co-authored-by:|claude-session:)|generated with \[claude code\]",
    re.IGNORECASE,
)


def offending_lines(log: str) -> list[tuple[str, str]]:
    """Return (commit, line) pairs for every forbidden line in ``git log`` output.

    ``log`` is the output of ``git log --format=%h%x00%B%x1e``.
    """
    found = []
    for record in log.split("\x1e"):
        if "\x00" not in record:
            continue
        commit, body = record.split("\x00", 1)
        for line in body.splitlines():
            if FORBIDDEN.search(line.strip()):
                found.append((commit.strip(), line.strip()))
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    log = subprocess.run(
        ["git", "log", "--format=%h%x00%B%x1e", argv[1]],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found = offending_lines(log)
    for commit, line in found:
        print(f"{commit}: {line}")
    if found:
        print(
            f"{len(found)} forbidden trailer line(s). Remove them from the commit messages before merging.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
