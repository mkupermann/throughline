"""Throughline's own `claude -p` calls must be identifiable by location.

Claude Code names a project directory after the calling process's working
directory, so sub-calls that inherit the repo checkout land inside the user's
real project history. Nothing about their *location* then distinguishes them
from work the user did, which is why they were caught by matching prompt
wording — a guess about text that had already missed 642 transcripts written
under an earlier phrasing.

Giving those calls a working directory of their own makes the distinction
structural. These tests pin the slug rule (Claude Code's, not ours) and the
exclusion that depends on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter
from throughline.self_referential import agent_call_cwd, is_agent_call_transcript


@pytest.fixture
def agent_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "agent-calls"
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", str(d))
    return d


def test_cwd_is_created_on_demand(agent_dir):
    """The scripts pass this straight to subprocess, which needs it to exist."""
    assert agent_call_cwd() == agent_dir
    assert agent_dir.is_dir()


def test_slug_matches_claude_codes_rule(agent_dir, tmp_path):
    """Every '/' and '.' becomes '-'.

    Verified against real directories on disk: ``/Users/x/.claude/sessions``
    is filed as ``-Users-x--claude-sessions`` — the doubled dash comes from the
    dot, and getting that wrong would silently exclude nothing.
    """
    slug = str(agent_dir).replace("/", "-").replace(".", "-")
    assert is_agent_call_transcript(Path(slug) / "session.jsonl")
    assert not is_agent_call_transcript(
        Path("-Users-someone-Documents-GitHub-throughline") / "session.jsonl"
    )


def test_dotted_directory_produces_a_doubled_dash(tmp_path, monkeypatch):
    """The ``~/.throughline`` default is a dotted path — the case that bites."""
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", "/home/u/.throughline/agent-calls")
    assert is_agent_call_transcript(
        Path("-home-u--throughline-agent-calls") / "s.jsonl"
    ), "the dot must slug to a dash, yielding a doubled dash after the '/'"


def test_the_predicate_creates_nothing(tmp_path, monkeypatch):
    """Classifying a path must not touch the filesystem.

    The predicate runs once per discovered file. An earlier version derived the
    slug from the create-on-demand helper, so merely asking "is this ours?"
    made a directory — and raised outright when the location was not writable,
    turning a read-only classification into an I/O error.
    """
    target = tmp_path / "never" / "created"
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", str(target))

    assert is_agent_call_transcript(Path("whatever") / "s.jsonl") is False
    assert not target.exists(), "the predicate created its own directory"


def test_adapter_excludes_agent_call_transcripts(agent_dir, tmp_path, monkeypatch):
    """A transcript in our own project folder is counted, never ingested."""
    home = tmp_path / "projects"
    slug = str(agent_dir).replace("/", "-").replace(".", "-")
    ours = home / slug
    theirs = home / "-Users-someone-Documents-GitHub-realwork"
    ours.mkdir(parents=True)
    theirs.mkdir(parents=True)
    (ours / "a.jsonl").write_text("{}", encoding="utf-8")
    (theirs / "b.jsonl").write_text("{}", encoding="utf-8")

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "home", home)

    assert adapter.excluded_reason(ours / "a.jsonl") == "throughline agent call"
    assert adapter.excluded_reason(theirs / "b.jsonl") is None

    # Coverage still sees it; ingestion does not. Losing that distinction would
    # make the provider bar under-report what is on disk.
    discovered = {p.name for p in adapter.discover_all()}
    ingestable = {p.name for p in adapter.discover()}
    assert discovered == {"a.jsonl", "b.jsonl"}
    assert ingestable == {"b.jsonl"}
