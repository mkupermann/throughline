"""Subagent transcripts are counted but never ingested.

They are not noise — 12-16 messages each, averaging 730 KB, containing work
that exists nowhere else. They are excluded for a correctness reason: a
subagent inherits its parent's `sessionId`, and the writer keys on it.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter


def _write_session(path: Path, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": str(uuid.uuid4()),
                "parentUuid": None,
                "isSidechain": False,
                "sessionId": session_id,
                "message": {"role": "user", "content": "hello"},
                "timestamp": "2026-01-15T10:00:00Z",
            }
        )
        + "\n"
    )


@pytest.fixture()
def projects(tmp_path, monkeypatch):
    """A parent session, three direct subagents, and two nested under a workflow.

    The nested shape is real: on the author's machine 26 files live at
    ``<session>/subagents/workflows/wf_<id>/agent-*.jsonl``, where the
    immediate parent directory is the workflow, not ``subagents``.
    """
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    _write_session(home / "-Users-x" / f"{sid}.jsonl", sid)
    for i in range(3):
        _write_session(home / "-Users-x" / sid / "subagents" / f"agent-{i}.jsonl", sid)
    for i in range(2):
        _write_session(
            home / "-Users-x" / sid / "subagents" / "workflows" / "wf_abc" / f"agent-{i}.jsonl",
            sid,
        )
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)
    return adapter, sid


def test_discover_all_reaches_the_deeper_files(projects):
    """The old `proj.glob('*.jsonl')` could not see 132 of 259 files."""
    adapter, _ = projects
    assert len(list(adapter.discover_all())) == 6


def test_discover_excludes_subagents(projects):
    """Bar 4a: only the parent is offered to the writer."""
    adapter, _ = projects
    found = list(adapter.discover())
    assert len(found) == 1
    assert "subagents" not in str(found[0])


def test_excluded_reason_explains_itself(projects):
    adapter, sid = projects
    sub = adapter.home / "-Users-x" / sid / "subagents" / "agent-0.jsonl"
    parent = adapter.home / "-Users-x" / f"{sid}.jsonl"
    assert adapter.excluded_reason(sub) == "subagent transcript"
    assert adapter.excluded_reason(parent) is None


def test_nested_workflow_subagents_are_also_excluded(projects):
    """The regression guard for the narrow rule.

    `path.parent.name == "subagents"` passes every other test in this file and
    still lets these through — 26 such files exist on the author's machine, 25
    of which share a sessionId with a top-level file. Excluding by "subagents
    anywhere in the relative path" is what makes this pass.
    """
    adapter, sid = projects
    nested = adapter.home / "-Users-x" / sid / "subagents" / "workflows" / "wf_abc" / "agent-0.jsonl"
    assert adapter.excluded_reason(nested) == "subagent transcript"
    # Compare relative to `home`, not the absolute path: pytest's `tmp_path`
    # fixture names the temp dir after the test function, and this test's
    # own name contains the substring "subagents" — an absolute-path
    # substring check would false-fail on every file here regardless of
    # whether the exclusion logic is correct.
    assert all("subagents" not in str(p.relative_to(adapter.home)) for p in adapter.discover())


def test_is_present_is_false_for_an_empty_directory(tmp_path, monkeypatch):
    """Spec §4.4 — this is what makes cline report no_data instead of present."""
    empty = tmp_path / "projects"
    empty.mkdir()
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", empty)
    assert adapter.is_present() is False


def test_is_present_is_true_when_a_file_exists(projects):
    adapter, _ = projects
    assert adapter.is_present() is True


# --- Symlink hardening -------------------------------------------------
#
# These call excluded_reason() directly on the symlinked path rather than
# going through discover(). On Python 3.13+, rglob("*.jsonl") no longer
# follows directory symlinks by default (recurse_symlinks=False), so a
# discover()-based test of case (b) below would pass vacuously on this
# interpreter and give zero protection on the 3.12 CI runner (this repo's
# floor is Python 3.10). A symlinked *file* (case (a)) reaches discover()
# on every Python version regardless, since only directory-symlink
# recursion changed — but testing excluded_reason() directly is the same
# either way and isn't version-sensitive.


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="requires Windows symlink privileges; the symlink boundary is covered by Linux CI",
)
def test_symlinked_file_pointing_into_subagents_is_excluded(tmp_path, monkeypatch):
    """(a) A symlink FILE at project top level -> real subagents/agent-0.jsonl.

    The symlink's own path carries no "subagents" segment, so the literal
    rel.parts check alone would miss it and let it reach the writer.
    """
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    real = home / "-Users-x" / sid / "subagents" / "agent-0.jsonl"
    _write_session(real, sid)
    link = home / "-Users-x" / "link-to-agent.jsonl"
    link.symlink_to(real)

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)

    assert "subagents" not in link.relative_to(home).parts
    assert adapter.excluded_reason(link) == "subagent transcript"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="requires Windows symlink privileges; the symlink boundary is covered by Linux CI",
)
def test_symlinked_dir_pointing_into_subagents_is_excluded(tmp_path, monkeypatch):
    """(b) A symlinked DIR named "alias" (not "subagents") -> the real subagents/ dir.

    Walking through the alias never puts the literal string "subagents" in
    the path being tested; only resolving the symlink reveals it.
    """
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    real_dir = home / "-Users-x" / sid / "subagents"
    _write_session(real_dir / "agent-0.jsonl", sid)
    alias = home / "-Users-x" / "alias"
    alias.symlink_to(real_dir, target_is_directory=True)
    path_through_alias = alias / "agent-0.jsonl"

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)

    assert "subagents" not in path_through_alias.relative_to(home).parts
    assert adapter.excluded_reason(path_through_alias) == "subagent transcript"


def test_real_file_literally_named_subagents_dot_jsonl_is_still_ingested(tmp_path, monkeypatch):
    """(c) A genuine (non-symlink) session file named "subagents.jsonl" is not a
    directory called "subagents" — it must still be ingested."""
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    f = home / "-Users-x" / "subagents.jsonl"
    _write_session(f, sid)

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)

    assert adapter.excluded_reason(f) is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="requires Windows symlink privileges; broken-link handling is covered by Linux CI",
)
def test_broken_symlink_is_excluded_without_raising(tmp_path, monkeypatch):
    """(d) A symlink whose target does not exist must be excluded, not raise."""
    home = tmp_path / "projects"
    (home / "-Users-x").mkdir(parents=True)
    link = home / "-Users-x" / "broken.jsonl"
    link.symlink_to(home / "-Users-x" / "does-not-exist.jsonl")

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)

    assert adapter.excluded_reason(link) is not None
