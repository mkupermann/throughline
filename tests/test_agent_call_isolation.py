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

import sys
from pathlib import Path, PurePosixPath

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter
from throughline.self_referential import agent_call_cwd, is_agent_call_transcript

POSIX_CLAUDE_SLUG_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Claude Code's POSIX project-slug convention is covered by Linux CI",
)


@pytest.fixture
def agent_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "agent-calls"
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", str(d))
    return d


def test_cwd_is_created_on_demand(agent_dir):
    """The scripts pass this straight to subprocess, which needs it to exist."""
    assert agent_call_cwd() == agent_dir
    assert agent_dir.is_dir()


@POSIX_CLAUDE_SLUG_ONLY
def test_slug_matches_claude_codes_rule(monkeypatch):
    """Every '/' and '.' becomes '-'.

    Verified against real directories on disk: ``/Users/x/.claude/sessions``
    is filed as ``-Users-x--claude-sessions`` — the doubled dash comes from the
    dot, and getting that wrong would silently exclude nothing.
    """
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", "/Users/x/.claude/sessions")
    slug = "-Users-x--claude-sessions"
    assert is_agent_call_transcript(PurePosixPath(slug) / "session.jsonl")
    assert not is_agent_call_transcript(PurePosixPath("-Users-someone-Documents-GitHub-throughline") / "session.jsonl")


@POSIX_CLAUDE_SLUG_ONLY
def test_dotted_directory_produces_a_doubled_dash(tmp_path, monkeypatch):
    """The ``~/.throughline`` default is a dotted path — the case that bites."""
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", "/home/u/.throughline/agent-calls")
    assert is_agent_call_transcript(
        PurePosixPath("-home-u--throughline-agent-calls") / "s.jsonl"
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


@POSIX_CLAUDE_SLUG_ONLY
def test_adapter_excludes_agent_call_transcripts(tmp_path, monkeypatch):
    """A transcript in our own project folder is counted, never ingested."""
    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", "/Users/test/.throughline/agent-calls")
    home = tmp_path / "projects"
    slug = "-Users-test--throughline-agent-calls"
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


# --------------------------------------------------------------------------- #
# Recognising our own calls on someone else's machine                         #
# --------------------------------------------------------------------------- #


def test_our_own_calls_are_recognised_whatever_home_wrote_them(monkeypatch):
    """The check slugged the *absolute* path of this process's home.

    A container reading transcripts the host wrote computed
    `-home-throughline--throughline-agent-calls` and compared it against
    `-Users-alice--throughline-agent-calls`: no match, so Throughline's
    own model calls were ingested as the user's work. Fourteen of them, on the
    corpus where this was found. The same happens on any second machine, and
    replication would then carry the mistake to both.
    """
    from pathlib import Path

    from throughline.self_referential import is_agent_call_transcript

    monkeypatch.delenv("THROUGHLINE_AGENT_CALL_DIR", raising=False)
    for home in ("-Users-alice", "-home-throughline", "-home-bob"):
        transcript = Path(f"/anywhere/projects/{home}--throughline-agent-calls/abc.jsonl")
        assert is_agent_call_transcript(transcript), home


def test_a_real_project_is_not_mistaken_for_one_of_our_calls(monkeypatch):
    from pathlib import Path

    from throughline.self_referential import is_agent_call_transcript

    monkeypatch.delenv("THROUGHLINE_AGENT_CALL_DIR", raising=False)
    for name in (
        "-Users-alice-Documents-GitHub-throughline",
        "-Users-alice--throughline",
        "-Users-alice-agent-calls",
    ):
        assert not is_agent_call_transcript(Path(f"/p/{name}/abc.jsonl")), name


@POSIX_CLAUDE_SLUG_ONLY
def test_an_explicit_override_is_still_matched_exactly(monkeypatch):
    from throughline.self_referential import is_agent_call_transcript

    monkeypatch.setenv("THROUGHLINE_AGENT_CALL_DIR", "/srv/throughline/woanders")
    slug = "-srv-throughline-woanders"
    assert is_agent_call_transcript(PurePosixPath("/p") / slug / "abc.jsonl")
