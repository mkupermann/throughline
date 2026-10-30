#!/usr/bin/env python3
"""Refuse changes that would publish blocked terms in this public repository.

The terms (customer and employer names, personal paths) live outside the
repository: in the ``BLOCKED_TERMS`` environment variable, one per line (the
CI job fills it from a repository secret), or in the file named by
``BLOCKED_TERMS_FILE``. The report never prints a term or the matching text,
only where it is and which list entry matched, so the check cannot leak the
list it protects.

Checked: the lines a revision range adds, the paths it adds or renames, its
commit messages, and the pull request title and body in ``PR_TITLE`` and
``PR_BODY`` when they are set.

Usage::

    python3 scripts/check_blocked_terms.py <git revision range>

Without any terms the check fails, unless ``ALLOW_EMPTY_TERMS=1`` (set by CI
for pull requests from forks, which cannot read repository secrets).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def load_terms(env: dict[str, str]) -> list[str]:
    """Terms from ``BLOCKED_TERMS`` or ``BLOCKED_TERMS_FILE``, lowercased.

    Blank lines and lines starting with ``#`` are skipped.
    """
    text = env.get("BLOCKED_TERMS", "")
    path = env.get("BLOCKED_TERMS_FILE", "")
    if not text.strip() and path:
        text = Path(path).read_text(encoding="utf-8")
    terms = []
    for line in text.splitlines():
        term = line.strip().lower()
        if term and not term.startswith("#"):
            terms.append(term)
    return terms


def compile_terms(terms: list[str]) -> list[re.Pattern[str]]:
    """One pattern per term; a term only matches as a whole word.

    ``acme`` matches ``ACME``, ``acme-ca`` and ``Acme.Corp``, not ``macme``.
    """
    return [re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", re.IGNORECASE) for t in terms]


def find(patterns: list[re.Pattern[str]], items: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """``(where, entry number)`` for every item whose text matches an entry.

    Entry numbers start at 1, in list order.
    """
    found = []
    for where, text in items:
        for number, pattern in enumerate(patterns, start=1):
            if pattern.search(text):
                found.append((where, number))
    return found


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(diff: str) -> list[tuple[str, str]]:
    """``(path:line, text)`` for every added line, plus ``(path, path)`` per file.

    ``diff`` is ``git diff --unified=0 --no-color`` output.
    """
    items: list[tuple[str, str]] = []
    path = ""
    line = 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[4:]
            path = path[2:] if path.startswith("b/") else path
            if path != "/dev/null":
                items.append((path, path))
            continue
        hunk = _HUNK.match(row)
        if hunk:
            line = int(hunk.group(1))
            continue
        if row.startswith("+") and path and path != "/dev/null":
            items.append((f"{path}:{line}", row[1:]))
            line += 1
    return items


def commit_messages(log: str) -> list[tuple[str, str]]:
    """``(commit sha, message)`` from ``git log --format=%h%x00%B%x1e``."""
    items = []
    for record in log.split("\x1e"):
        if "\x00" in record:
            commit, body = record.split("\x00", 1)
            items.append((f"commit {commit.strip()}", body))
    return items


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(argv: list[str], env: dict[str, str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    terms = load_terms(env)
    if not terms:
        if env.get("ALLOW_EMPTY_TERMS") == "1":
            print("No blocked terms available (fork pull request); skipping.", file=sys.stderr)
            return 0
        print("No blocked terms configured: set BLOCKED_TERMS or BLOCKED_TERMS_FILE.", file=sys.stderr)
        return 2
    rng = argv[1]
    diff_range = rng if ".." in rng else f"{rng}^!"
    try:
        diff = _git("diff", "--unified=0", "--no-color", "-M", diff_range)
    except subprocess.CalledProcessError:
        # A root commit has no parent for `^!`.
        diff = _git("show", "--unified=0", "--no-color", "--format=", rng)
    log_range = [rng] if ".." in rng else ["-1", rng]
    log = _git("log", "--format=%h%x00%B%x1e", *log_range)
    items = added_lines(diff) + commit_messages(log)
    for name in ("PR_TITLE", "PR_BODY"):
        if env.get(name):
            items.append((name.lower().replace("_", " "), env[name]))
    found = find(compile_terms(terms), items)
    for where, number in found:
        print(f"{where}: blocked term #{number}")
    if found:
        print(
            f"{len(found)} blocked term match(es). Remove them before this reaches the public repository.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv, dict(os.environ)))
