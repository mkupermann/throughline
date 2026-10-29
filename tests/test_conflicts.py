"""Tests for ``throughline.conflicts``.

Unit-test the pure-Python parts (heuristics, dataclasses, formatter) and
exercise the public API with a mocked DB connection so we don't require a
live Postgres in CI. The SQL itself is integration-tested elsewhere (or
in a future fixture-backed integration suite).
"""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from throughline import conflicts
from throughline.conflicts import (
    Conflict,
    ConflictChunk,
    ConflictReport,
    _has_contradiction_marker,
    find_conflicts,
    format_human,
)

# ---------------------------------------------------------------------------
# Contradiction markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "We rolled back to SQLAlchemy",
        "Switched from Milvus to pgvector",
        "Actually we should use Postgres",
        "The Redis approach was deprecated last sprint",
        "Replaced by a simpler in-memory cache",
        "no longer using Celery",
    ],
)
def test_marker_detection_positive(text: str) -> None:
    assert _has_contradiction_marker(text), f"should match: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "We picked pgvector for the audit pipeline",
        "Migration ran cleanly in production",
        "",
        None,
        "Just a regular note about the project structure.",
    ],
)
def test_marker_detection_negative(text) -> None:
    assert not _has_contradiction_marker(text or ""), f"should NOT match: {text!r}"


def test_marker_detection_case_insensitive() -> None:
    assert _has_contradiction_marker("ROLLED BACK to the previous schema")
    assert _has_contradiction_marker("ReJecTeD the proposal")


# ---------------------------------------------------------------------------
# Dataclasses round-trip
# ---------------------------------------------------------------------------


def _mk_chunk(chunk_id: int = 1, tool: str = "claude_code") -> ConflictChunk:
    return ConflictChunk(
        chunk_id=chunk_id,
        tool=tool,
        project="myproj",
        category="decision",
        content="some text",
        created_at="2026-01-01T00:00:00+00:00",
        status="active",
    )


def test_chunk_serializes() -> None:
    c = _mk_chunk()
    d = c.to_dict()
    assert d["chunk_id"] == 1
    assert d["tool"] == "claude_code"
    assert d["category"] == "decision"


def test_conflict_serializes() -> None:
    c = Conflict(
        kind="supersession",
        confidence=1.0,
        a=_mk_chunk(1, "claude_code"),
        b=_mk_chunk(2, "codex"),
        why="example",
    )
    d = c.to_dict()
    assert d["kind"] == "supersession"
    assert d["a"]["tool"] == "claude_code"
    assert d["b"]["tool"] == "codex"
    assert d["confidence"] == 1.0


def test_report_summary() -> None:
    rep = ConflictReport(
        conflicts=[
            Conflict("supersession", 1.0, _mk_chunk(1), _mk_chunk(2), "x"),
            Conflict("supersession", 1.0, _mk_chunk(3), _mk_chunk(4), "x"),
            Conflict("semantic", 0.92, _mk_chunk(5), _mk_chunk(6), "x"),
            Conflict("stale_drift", 0.5, _mk_chunk(7), _mk_chunk(8), "x"),
        ]
    )
    assert rep.by_kind == {"supersession": 2, "semantic": 1, "stale_drift": 1}
    assert rep.to_dict()["summary"] == {
        "total": 4,
        "by_kind": {"supersession": 2, "semantic": 1, "stale_drift": 1},
    }


# ---------------------------------------------------------------------------
# Human formatter
# ---------------------------------------------------------------------------


def test_format_human_empty_report_db_reachable() -> None:
    rep = ConflictReport(db_reachable=True, params={"project": None})
    txt = format_human(rep)
    assert "No cross-tool conflicts found" in txt
    assert "--since-days 90" in txt  # remediation hint


def test_format_human_db_unreachable() -> None:
    rep = ConflictReport(db_reachable=False, error="connection refused")
    txt = format_human(rep)
    assert "Cannot reach the memory DB" in txt
    assert "connection refused" in txt


def test_format_human_groups_by_kind() -> None:
    rep = ConflictReport(
        conflicts=[
            Conflict(
                "supersession",
                1.0,
                _mk_chunk(1, "claude_code"),
                _mk_chunk(2, "codex"),
                "chunk #1 (from claude_code) was explicitly superseded by chunk #2 (from codex)",
            ),
            Conflict(
                "semantic",
                0.91,
                _mk_chunk(3, "hermes"),
                _mk_chunk(4, "claude_code"),
                "hermes and claude_code agree-ish; newer rejected",
            ),
        ]
    )
    txt = format_human(rep)
    assert "Documented supersession (1)" in txt
    assert "Semantic near-duplicate" in txt
    assert "claude_code → codex" in txt
    assert "hermes → claude_code" in txt
    assert "Summary: 2 conflict(s)" in txt


# ---------------------------------------------------------------------------
# find_conflicts — DB-unreachable path
# ---------------------------------------------------------------------------


def test_find_conflicts_returns_unreachable_when_connect_fails() -> None:
    """When _connect returns None (no psycopg2 / DB down), report says so."""
    with patch.object(conflicts, "_connect", return_value=None):
        rep = find_conflicts()
    assert rep.db_reachable is False
    assert "DB unreachable" in (rep.error or "")
    assert rep.conflicts == []
    # Params are still echoed so consumers know what was asked for.
    assert "supersession" in rep.params["kinds"]
    assert "semantic" in rep.params["kinds"]
    assert "stale_drift" in rep.params["kinds"]


def test_find_conflicts_kinds_filter_echoed_in_params() -> None:
    with patch.object(conflicts, "_connect", return_value=None):
        rep = find_conflicts(kinds=["supersession"])
    assert rep.params["kinds"] == ["supersession"]


# ---------------------------------------------------------------------------
# find_conflicts — mocked DB cursor returning empty results
# ---------------------------------------------------------------------------


class _MockCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []
        self._results: list = []

    def __enter__(self) -> _MockCursor:
        return self

    def __exit__(self, *a) -> None:
        pass

    def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, dict(params or {})))
        # default: no rows
        self._results = []

    def fetchall(self) -> list:
        return self._results


class _MockConn:
    def __init__(self) -> None:
        self._cur = _MockCursor()

    def cursor(self) -> _MockCursor:
        return self._cur

    def close(self) -> None:
        pass


def test_find_conflicts_runs_all_three_strategies_by_default() -> None:
    conn = _MockConn()
    rep = find_conflicts(conn=conn)
    assert rep.db_reachable is True
    assert rep.conflicts == []
    # The semantic strategy attempts both embedding columns, so we expect at least
    # 1 (supersession) + 2 (semantic 768 + 1536) + 1 (stale_drift) = 4 queries.
    assert len(conn.cursor().executed) >= 4


def test_find_conflicts_kind_filter_skips_others() -> None:
    conn = _MockConn()
    rep = find_conflicts(conn=conn, kinds=["supersession"])
    assert rep.db_reachable is True
    # Exactly one SQL should have run (the supersession query).
    assert len(conn.cursor().executed) == 1
    sql, _params = conn.cursor().executed[0]
    assert "superseded_by" in sql


# ---------------------------------------------------------------------------
# End-to-end CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_conflicts_json_smoke() -> None:
    """``python -m throughline.cli conflicts --json`` must parse cleanly even
    when no DB is reachable."""
    result = subprocess.run(
        [sys.executable, "-m", "throughline.cli", "conflicts", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    assert "conflicts" in payload
    assert "summary" in payload
    assert "params" in payload
    assert isinstance(payload["params"]["kinds"], list)


def test_cli_conflicts_kind_filter_via_argv() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "throughline.cli",
            "conflicts",
            "--json",
            "--kind",
            "supersession",
            "--kind",
            "semantic",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    assert payload["params"]["kinds"] == ["semantic", "supersession"]
