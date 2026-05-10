"""DB-free tests for ``scripts/repair_conversations.py`` and the token
helpers in ``scripts/ingest_sessions.py``.

We never connect to Postgres here. The repair logic operates on a fake
connection / cursor, and the ingest helpers (``_first_cwd``,
``_per_message_total``, ``_sum_usage``) are pure functions over Python
dicts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


# ── Fakes ────────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []
        self.executed: list[tuple[str, tuple | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self):
        self.cursors: list[_FakeCursor] = []
        self.committed = 0

    def push(self, cur: _FakeCursor):
        self.cursors.append(cur)

    def cursor(self):
        return self.cursors.pop(0)

    def commit(self):
        self.committed += 1

    def close(self):
        pass


# ── Ingest token helpers ─────────────────────────────────────────────────────
def test_first_cwd_returns_first_non_empty():
    import ingest_sessions as ing
    entries = [
        {"type": "user", "cwd": ""},
        {"type": "user", "cwd": None},
        {"type": "user"},
        {"type": "user", "cwd": "/Users/me/repo"},
        {"type": "user", "cwd": "/Users/me/other"},
    ]
    assert ing._first_cwd(entries) == "/Users/me/repo"


def test_first_cwd_returns_none_when_absent():
    import ingest_sessions as ing
    entries = [{"type": "user"}, {"type": "user", "cwd": ""}]
    assert ing._first_cwd(entries) is None


def test_per_message_total_sums_all_usage_categories():
    import ingest_sessions as ing
    msg = {
        "role": "assistant",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 25,
        },
    }
    # 100 + 200 + 50 + 25 = 375
    assert ing._per_message_total(msg) == 375


def test_per_message_total_handles_missing_usage():
    import ingest_sessions as ing
    assert ing._per_message_total({"role": "user"}) is None
    assert ing._per_message_total({"role": "assistant", "usage": None}) is None
    assert ing._per_message_total({"role": "assistant", "usage": "weird"}) is None


def test_sum_usage_aggregates_across_assistant_entries_only():
    import ingest_sessions as ing
    entries = [
        {"message": {"role": "user"}},  # no usage block
        {"message": {"role": "assistant",
                     "usage": {"input_tokens": 10, "output_tokens": 20}}},
        {"message": {"role": "assistant",
                     "usage": {"input_tokens": 5,
                               "cache_creation_input_tokens": 3,
                               "output_tokens": 7}}},
        {"no_message_key": True},
    ]
    in_, out = ing._sum_usage(entries)
    assert in_ == 10 + 5 + 3   # cache_creation counts as input
    assert out == 20 + 7


# ── Repair: parse_jsonl ──────────────────────────────────────────────────────
def test_parse_jsonl_extracts_cwd_session_id_and_tokens(tmp_path):
    import repair_conversations as rep

    f = tmp_path / "session.jsonl"
    f.write_text(
        "\n".join([
            json.dumps({"type": "user", "sessionId": "abc-123",
                        "cwd": "/Users/me/repo",
                        "message": {"role": "user", "content": "hi"}}),
            json.dumps({"type": "assistant", "sessionId": "abc-123",
                        "message": {"role": "assistant",
                                    "usage": {"input_tokens": 10,
                                              "output_tokens": 5,
                                              "cache_read_input_tokens": 2}}}),
            "",  # blank line tolerated
            "{not valid json",  # malformed line tolerated
            json.dumps({"type": "assistant",
                        "message": {"role": "assistant",
                                    "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        ]),
        encoding="utf-8",
    )
    cwd, t_in, t_out, sid = rep.parse_jsonl(f)
    assert cwd == "/Users/me/repo"
    assert sid == "abc-123"
    # 10 + 2 (cache_read) + 1 = 13 input
    assert t_in == 13
    # 5 + 1 = 6 output
    assert t_out == 6


def test_parse_jsonl_missing_file_returns_empty():
    import repair_conversations as rep
    cwd, t_in, t_out, sid = rep.parse_jsonl(Path("/nonexistent/path.jsonl"))
    assert (cwd, t_in, t_out, sid) == (None, 0, 0, None)


# ── Repair: repair_session ───────────────────────────────────────────────────
def test_repair_session_aggregates_across_files(tmp_path, monkeypatch):
    import repair_conversations as rep

    parent = tmp_path / "parent.jsonl"
    parent.write_text(
        json.dumps({"type": "user", "sessionId": "s1",
                    "cwd": "/Users/me/repo",
                    "message": {"role": "assistant",
                                "usage": {"input_tokens": 100, "output_tokens": 50}}}),
        encoding="utf-8",
    )
    sub = tmp_path / "subagent-x.jsonl"
    sub.write_text(
        json.dumps({"sessionId": "s1",
                    "message": {"role": "assistant",
                                "usage": {"input_tokens": 200, "output_tokens": 75}}}),
        encoding="utf-8",
    )

    conn = _FakeConn()
    # Lookup row: id=42, current path is wrong, tokens are zero.
    conn.push(_FakeCursor(row=(42, "/wrong/path", 0, 0)))
    # Update cursor.
    conn.push(_FakeCursor())

    res = rep.repair_session(conn, session_id="s1",
                             file_paths=[str(parent), str(sub)],
                             dry_run=False)
    assert res["status"] == "updated"
    assert res["id"] == 42
    assert res["changes"]["project_path"] == ("/wrong/path", "/Users/me/repo")
    # 100 + 200 = 300 in; 50 + 75 = 125 out
    assert res["changes"]["token_count_in"] == (0, 300)
    assert res["changes"]["token_count_out"] == (0, 125)
    assert conn.committed == 1


def test_repair_session_dry_run_does_not_write(tmp_path):
    import repair_conversations as rep

    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({"sessionId": "s2", "cwd": "/x",
                    "message": {"role": "assistant",
                                "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        encoding="utf-8",
    )
    conn = _FakeConn()
    conn.push(_FakeCursor(row=(7, None, 0, 0)))

    res = rep.repair_session(conn, session_id="s2",
                             file_paths=[str(f)], dry_run=True)
    assert res["status"] == "would-update"
    assert conn.committed == 0


def test_repair_session_no_change_when_already_correct(tmp_path):
    import repair_conversations as rep

    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({"sessionId": "s3", "cwd": "/already/correct",
                    "message": {"role": "assistant",
                                "usage": {"input_tokens": 100, "output_tokens": 50}}}),
        encoding="utf-8",
    )
    conn = _FakeConn()
    conn.push(_FakeCursor(row=(8, "/already/correct", 100, 50)))

    res = rep.repair_session(conn, session_id="s3",
                             file_paths=[str(f)], dry_run=False)
    assert res["status"] == "no-change"
    assert conn.committed == 0


def test_repair_session_skipped_when_row_missing(tmp_path):
    import repair_conversations as rep

    f = tmp_path / "s.jsonl"
    f.write_text(
        json.dumps({"sessionId": "missing", "cwd": "/x",
                    "message": {"role": "user", "content": "hi"}}),
        encoding="utf-8",
    )
    conn = _FakeConn()
    conn.push(_FakeCursor(row=None))

    res = rep.repair_session(conn, session_id="missing",
                             file_paths=[str(f)], dry_run=False)
    assert res["status"] == "skipped"
    assert res["reason"] == "row-not-found"


def test_repair_session_skipped_when_no_jsonl_readable():
    import repair_conversations as rep
    conn = _FakeConn()
    res = rep.repair_session(conn, session_id="absent",
                             file_paths=["/no/such/file"], dry_run=False)
    assert res["status"] == "skipped"
    assert res["reason"] == "no-readable-jsonl"
