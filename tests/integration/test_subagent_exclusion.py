"""Bar 4a: ingesting a parent plus its subagents keeps the parent whole.

This is the test that would have caught the hazard in design spec §9 before
it reached the database. Without the exclusion, the parent's messages are
deleted and replaced by the last subagent file processed, and the ingest
reports success.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from throughline.adapters.claude_code import ClaudeCodeAdapter
from throughline.adapters.writer import run_adapter

pytestmark = pytest.mark.integration


def _write(path: Path, session_id: str, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, t in enumerate(texts):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "parentUuid": None,
                    "isSidechain": False,
                    "sessionId": session_id,
                    "message": {"role": "user", "content": t},
                    "timestamp": f"2026-01-15T10:0{i}:00Z",
                }
            )
        )
    path.write_text("\n".join(lines) + "\n")


def test_parent_session_keeps_all_its_messages(tmp_path, monkeypatch, db_connection):
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    _write(home / "-Users-x" / f"{sid}.jsonl", sid, ["p1", "p2", "p3", "p4", "p5"])
    for i in range(3):
        _write(home / "-Users-x" / sid / "subagents" / f"agent-{i}.jsonl", sid, ["sub"])

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)

    assert len(list(adapter.discover_all())) == 4, "all four files must be countable"
    assert len(list(adapter.discover())) == 1, "only the parent may be ingested"

    run_adapter(adapter, conn=db_connection, verbose=False)

    with db_connection.cursor() as cur:
        cur.execute("SELECT id, message_count FROM conversations WHERE session_id=%s", (sid,))
        row = cur.fetchone()
        assert row is not None, "the parent session must exist"
        conv_id, count = row
        cur.execute("SELECT count(*) FROM messages WHERE conversation_id=%s", (conv_id,))
        actual = cur.fetchone()[0]

    assert actual == 5, f"parent lost messages: {actual} of 5 survived"
    assert count == 5


def test_exactly_one_conversation_per_parent_session(tmp_path, monkeypatch, db_connection):
    """Bar 4a stated directly."""
    home = tmp_path / "projects"
    sid = str(uuid.uuid4())
    _write(home / "-Users-y" / f"{sid}.jsonl", sid, ["a"])
    for i in range(4):
        _write(home / "-Users-y" / sid / "subagents" / f"agent-{i}.jsonl", sid, ["b"])

    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(type(adapter), "home", home)
    run_adapter(adapter, conn=db_connection, verbose=False)

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations WHERE session_id=%s", (sid,))
        assert cur.fetchone()[0] == 1
