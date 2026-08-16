"""Unit tests for the Hermes state.db (SQLite) parsing path.

Builds a synthetic state.db with the same schema Hermes ships and
exercises ``_parse_state_db`` directly. End-to-end (DB + writer)
coverage of the multi-conversation-per-file case lives in the e2e
test harness.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from throughline.adapters.hermes import (
    _NS,
    HermesAdapter,
    _parse_state_db,
)

# Minimal Hermes-compatible schema. Mirrors the columns the adapter
# reads (it tolerates extras / missing optional cols via SELECT order).
_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    model TEXT,
    title TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    system_prompt TEXT,
    message_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    actual_cost_usd REAL,
    estimated_cost_usd REAL
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT
);
"""


def _make_db(tmp_path: Path, sessions: list[dict], messages: list[dict]) -> Path:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for s in sessions:
        cols = ",".join(s.keys())
        placeholders = ",".join(["?"] * len(s))
        conn.execute(f"INSERT INTO sessions ({cols}) VALUES ({placeholders})", tuple(s.values()))
    for m in messages:
        cols = ",".join(m.keys())
        placeholders = ",".join(["?"] * len(m))
        conn.execute(f"INSERT INTO messages ({cols}) VALUES ({placeholders})", tuple(m.values()))
    conn.commit()
    conn.close()
    return path


class TestParseStateDb:
    def test_emits_one_conversation_per_session_row(self, tmp_path):
        path = _make_db(
            tmp_path,
            sessions=[
                {
                    "id": "S1",
                    "source": "cli",
                    "model": "claude-opus-4-7",
                    "title": "First",
                    "started_at": 1700000000.0,
                    "ended_at": 1700000600.0,
                    "message_count": 2,
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
                {
                    "id": "S2",
                    "source": "cli",
                    "model": "claude-opus-4-7",
                    "title": "Second",
                    "started_at": 1700001000.0,
                    "ended_at": 1700001100.0,
                    "message_count": 1,
                },
            ],
            messages=[
                {"session_id": "S1", "role": "user", "content": "hi", "timestamp": 1700000010.0},
                {
                    "session_id": "S1",
                    "role": "assistant",
                    "content": "hello",
                    "timestamp": 1700000020.0,
                    "finish_reason": "stop",
                },
                {"session_id": "S2", "role": "user", "content": "only one", "timestamp": 1700001050.0},
            ],
        )
        out = _parse_state_db(path)
        assert len(out) == 2
        assert {c.summary for c in out} == {"First", "Second"}
        s1 = next(c for c in out if c.summary == "First")
        assert s1.project_path == "hermes"
        assert s1.model == "claude-opus-4-7"
        assert s1.token_count_in == 10
        assert s1.token_count_out == 5
        assert [m.role for m in s1.messages] == ["user", "assistant"]

    def test_session_id_matches_json_path_derivation(self, tmp_path):
        """The DB path and the JSON path must derive the same session_id
        for the same Hermes session, so they upsert into one DB row."""
        path = _make_db(
            tmp_path,
            sessions=[
                {"id": "20260511_160653_6f89a7", "source": "cli", "started_at": 1700000000.0, "message_count": 1}
            ],
            messages=[
                {"session_id": "20260511_160653_6f89a7", "role": "user", "content": "x", "timestamp": 1700000010.0}
            ],
        )
        out = _parse_state_db(path)
        assert len(out) == 1
        expected = str(uuid.uuid5(_NS, "hermes:20260511_160653_6f89a7"))
        assert out[0].session_id == expected

    def test_unknown_role_is_skipped(self, tmp_path):
        path = _make_db(
            tmp_path,
            sessions=[{"id": "S", "source": "cli", "started_at": 1.0, "message_count": 0}],
            messages=[
                {"session_id": "S", "role": "user", "content": "hi", "timestamp": 1.0},
                {"session_id": "S", "role": "developer", "content": "ignore me", "timestamp": 2.0},
                {"session_id": "S", "role": "assistant", "content": "ok", "timestamp": 3.0},
            ],
        )
        out = _parse_state_db(path)
        assert len(out) == 1
        assert [m.role for m in out[0].messages] == ["user", "assistant"]

    def test_tool_calls_blob_is_decoded(self, tmp_path):
        path = _make_db(
            tmp_path,
            sessions=[{"id": "S", "source": "cli", "started_at": 1.0, "message_count": 1}],
            messages=[
                {
                    "session_id": "S",
                    "role": "assistant",
                    "content": "",
                    "tool_calls": '[{"function": {"name": "shell", "arguments": "{\\"cmd\\": \\"ls\\"}"}}]',
                    "timestamp": 1.0,
                }
            ],
        )
        out = _parse_state_db(path)
        assert len(out) == 1
        m = out[0].messages[0]
        assert m.tool_calls and m.tool_calls[0]["tool_name"] == "shell"
        assert m.tool_name == "shell"

    def test_missing_tables_returns_empty_no_crash(self, tmp_path):
        # state.db from a Hermes version we don't recognise — schema lacks
        # the tables we read. Should fail soft and return [].
        path = tmp_path / "state.db"
        conn = sqlite3.connect(path)
        conn.executescript("CREATE TABLE foo (id INTEGER);")
        conn.close()
        out = _parse_state_db(path)
        assert out == []

    def test_nonexistent_file_returns_empty(self, tmp_path):
        assert _parse_state_db(tmp_path / "absent.db") == []


class TestHermesDiscoverWithDb:
    def test_state_db_appears_before_session_jsons(self, tmp_path, monkeypatch):
        """``discover`` must yield state.db before any session_*.json so the
        DB path runs first; that lets the JSON path act as a fallback /
        snapshot rather than authoritative."""
        # Build a fake ~/.hermes with both shapes.
        hermes_root = tmp_path / ".hermes"
        (hermes_root / "sessions").mkdir(parents=True)
        # state.db file (content doesn't matter for discover())
        (hermes_root / "state.db").write_bytes(b"\x00")
        # two session jsons
        (hermes_root / "sessions" / "session_a.json").write_text("{}")
        (hermes_root / "sessions" / "session_b.json").write_text("{}")

        a = HermesAdapter()
        # Patch the root resolver to our tmp tree.
        monkeypatch.setattr(
            type(a),
            "_hermes_root",
            property(lambda self: hermes_root),
        )
        order = [p.name for p in a.discover()]
        assert order[0] == "state.db"
        assert "session_a.json" in order and "session_b.json" in order
