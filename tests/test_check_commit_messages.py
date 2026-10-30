"""The commit-message check finds co-author and AI-session trailers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_commit_messages",
    Path(__file__).resolve().parents[1] / "scripts" / "check_commit_messages.py",
)
ccm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ccm)


def _log(*messages: tuple[str, str]) -> str:
    return "".join(f"{sha}\x00{body}\x1e" for sha, body in messages)


def test_trailers_are_found():
    log = _log(
        ("abc1234", "feat: x\n\nCo-Authored-By: Someone <a@b.c>\n"),
        ("def5678", "docs: y\n\nClaude-Session: https://example.invalid/s\n"),
        ("0aa0aa0", "fix: z\n\n🤖 Generated with [Claude Code](https://example.invalid)\n"),
    )
    found = ccm.offending_lines(log)
    assert [c for c, _ in found] == ["abc1234", "def5678", "0aa0aa0"]


def test_clean_messages_pass():
    log = _log(
        ("abc1234", "feat: import CLAUDE.md files\n\nThe co-authored text is quoted.\n"),
        ("def5678", "fix: nothing to see\n"),
    )
    assert ccm.offending_lines(log) == []
