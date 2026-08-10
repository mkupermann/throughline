"""Subagent transcripts are counted but never ingested.

They are not noise — 12-16 messages each, averaging 730 KB, containing work
that exists nowhere else. They are excluded for a correctness reason: a
subagent inherits its parent's `sessionId`, and the writer keys on it.
"""

from __future__ import annotations

import json
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
    nested = (
        adapter.home / "-Users-x" / sid / "subagents" / "workflows" / "wf_abc" / "agent-0.jsonl"
    )
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
