"""End-to-end integration test for the adapter framework.

Drops synthetic source files for every shipping adapter into a tmp
directory, points each adapter's ``home`` at that tmp tree, runs the
real writer against a real Postgres, and asserts the conversations /
messages / projects rows came out right.

This is the harness the project review flagged as missing — it covers
the full `discover -> parse -> writer -> ingestion_log -> conversations
+ messages -> projects backfill` path without mocking the DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import psycopg2
import psycopg2.extras
import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Synthetic source-file builders                                              #
# --------------------------------------------------------------------------- #


#: Minimum messages each adapter's fixture must produce end to end.
#:
#: Hermes is the odd one: its fixture is a state.db *and* a JSON export, so it
#: writes two conversations. Windsurf ingests a plan document rather than a
#: transcript, which is one message by design.
EXPECTED_MESSAGES = {
    "claude_code": 2,
    "hermes": 4,
    "codex": 2,
    "cursor": 2,
    "zed": 2,
    "vibe": 2,
    "continue": 2,
    "cline": 2,
    "windsurf": 1,
}


def _make_claude_code(root: Path) -> Path:
    """Build ~/.claude/projects/<slug>/*.jsonl style fixture."""
    proj = root / "-Users-acme-repo"
    proj.mkdir(parents=True)
    f = proj / "11111111-1111-1111-1111-111111111111.jsonl"
    entries = [
        {
            "type": "user",
            "sessionId": "11111111-1111-1111-1111-111111111111",
            "timestamp": "2026-01-01T10:00:00Z",
            "cwd": "/Users/acme/repo",
            "uuid": "22222222-2222-2222-2222-222222222222",
            "message": {"role": "user", "content": "fix the bug"},
        },
        {
            "type": "assistant",
            "sessionId": "11111111-1111-1111-1111-111111111111",
            "timestamp": "2026-01-01T10:00:05Z",
            "uuid": "33333333-3333-3333-3333-333333333333",
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": "Reading the file..."}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        },
    ]
    f.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return root


def _make_hermes(root: Path) -> Path:
    """Build ~/.hermes with both a state.db and a sessions/*.json."""
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    # state.db: one session, two messages.
    conn = sqlite3.connect(root / "state.db")
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, title TEXT,
            started_at REAL NOT NULL, ended_at REAL, system_prompt TEXT,
            message_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            actual_cost_usd REAL, estimated_cost_usd REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
            tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
            timestamp REAL NOT NULL, token_count INTEGER, finish_reason TEXT,
            reasoning TEXT, reasoning_content TEXT, reasoning_details TEXT
        );
        """)
    conn.execute(
        "INSERT INTO sessions (id, source, model, title, started_at, ended_at, message_count) "
        "VALUES ('hermes-S1', 'cli', 'claude-opus-4-7', 'DB-only session', 1700000000.0, 1700000600.0, 2)"
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        [
            ("hermes-S1", "user", "from state.db", 1700000010.0),
            ("hermes-S1", "assistant", "hi", 1700000020.0),
        ],
    )
    conn.commit()
    conn.close()
    # JSON export: a *different* session (no DB row), to prove both paths
    # produce conversations.
    (sessions / "session_X.json").write_text(
        json.dumps(
            {
                "session_id": "hermes-S2",
                "model": "claude-opus-4-7",
                "platform": "cli",
                "session_start": "2026-01-02T09:00:00",
                "last_updated": "2026-01-02T09:05:00",
                "system_prompt": "you are…",
                "tools": [],
                "messages": [
                    {"role": "user", "content": "json export only"},
                    {"role": "assistant", "content": "ack"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _make_codex(root: Path) -> Path:
    """~/.codex/sessions/<date>/rollout-*.jsonl"""
    day = root / "2026-01-01"
    day.mkdir(parents=True)
    f = day / "rollout-1700000000-codex-S.jsonl"
    events = [
        {"type": "session_meta", "session_id": "codex-S", "model": "gpt-5-codex", "cwd": "/repo/x"},
        {"type": "user_message", "content": "run tests"},
        {"type": "assistant_message", "content": "I will run pytest."},
    ]
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return root


def _make_cursor(root: Path) -> Path:
    """~/.cursor/sessions/*.jsonl — one JSON object per line."""
    f = root / "session_cursor1.jsonl"
    msgs = [
        {"role": "user", "content": "add a dark theme toggle", "message_id": "m1"},
        {"role": "assistant", "content": "Adding a theme provider.", "message_id": "m2"},
    ]
    f.write_text("\n".join(json.dumps(m) for m in msgs) + "\n", encoding="utf-8")
    return root


def _make_zed(root: Path) -> Path:
    """~/.zed/data/sessions/session_*.json — one object per session."""
    f = root / "session_zed1.json"
    f.write_text(
        json.dumps(
            {
                "id": "zed-1",
                "model": "zed-pro",
                "messages": [
                    {"role": "user", "content": "explain this trait"},
                    {"role": "assistant", "content": "It is a marker trait."},
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _make_vibe(root: Path) -> Path:
    """~/.vibe/logs/session/session_<date>_<time>_<hex>/ — a directory per session.

    The directory name has to match the adapter's pattern exactly; a session
    called `session_1` is not discovered, which is the kind of thing only an
    end-to-end run catches.
    """
    d = root / "session_20260101_120000_abc123"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "session_id": "session_20260101_120000_abc123",
                "start_time": "2026-01-01T12:00:00Z",
                "end_time": "2026-01-01T12:20:00Z",
                "model": "mistral-large-2",
                "environment": {"working_directory": "/repo/vibeproj"},
            }
        ),
        encoding="utf-8",
    )
    msgs = [
        {
            "role": "user",
            "content": "why is the build slow",
            "message_id": "msg_1",
            "timestamp": "2026-01-01T12:00:10Z",
        },
        {
            "role": "assistant",
            "content": "The bundler re-reads node_modules each run.",
            "message_id": "msg_2",
            "timestamp": "2026-01-01T12:00:40Z",
        },
    ]
    (d / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in msgs) + "\n", encoding="utf-8")
    return root


def _make_continue(root: Path) -> Path:
    """~/.continue/sessions/<id>.json — history under a `history` key."""
    f = root / "cont-1.json"
    f.write_text(
        json.dumps(
            {
                "sessionId": "cont-1",
                "title": "Extract the login helper",
                "history": [
                    {"role": "user", "content": "extract login()"},
                    {"role": "assistant", "content": "Moved it into auth/session.py."},
                ],
                "lastUpdated": 1_700_000_000,
            }
        ),
        encoding="utf-8",
    )
    return root


def _make_cline(root: Path) -> Path:
    """A Cline task directory: the unit of ingestion is the folder, not a file."""
    d = root / "1700000000001"
    d.mkdir(parents=True)
    (d / "api_conversation_history.json").write_text(
        json.dumps(
            [
                {"role": "user", "content": "rename the column"},
                {"role": "assistant", "content": "Writing the migration."},
            ]
        ),
        encoding="utf-8",
    )
    (d / "task_metadata.json").write_text(json.dumps({"cwd": "/repo/clineproj"}), encoding="utf-8")
    return root


def _make_windsurf(root: Path) -> Path:
    """~/.windsurf/plans/*.md — a plan document, not a transcript.

    Body must clear the adapter's 50-character floor or the file is skipped as
    too thin to be worth storing.
    """
    f = root / "plan-ship-auth-a1b2c3.md"
    f.write_text(
        "# Ship the new auth flow\n\n"
        "Migrate token storage off localStorage before the next release.\n"
        "1. Spike the new endpoint.\n2. Wire the cookie path.\n",
        encoding="utf-8",
    )
    return root


def _patch_adapter_homes(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Repoint every shipping adapter at a per-tool subdir under tmp_path.

    Returns the directory mapping so the test can populate fixtures.
    """
    homes = {
        "claude_code": tmp_path / "claude" / "projects",
        "hermes": tmp_path / "hermes",
        "codex": tmp_path / "codex" / "sessions",
        "continue": tmp_path / "continue" / "sessions",
        "windsurf": tmp_path / "windsurf" / "plans",
        "cline": tmp_path / "cline_tasks",
        "vibe": tmp_path / "vibe" / "logs" / "session",
        "cursor": tmp_path / "cursor" / "sessions",
        "zed": tmp_path / "zed" / "data" / "sessions",
    }
    for d in homes.values():
        d.mkdir(parents=True, exist_ok=True)

    from throughline.adapters import hermes as hermes_mod
    from throughline.adapters.claude_code import ClaudeCodeAdapter
    from throughline.adapters.cline import ClineAdapter
    from throughline.adapters.codex import CodexAdapter
    from throughline.adapters.continue_dev import ContinueDevAdapter
    from throughline.adapters.cursor import CursorAdapter
    from throughline.adapters.vibe import VibeAdapter
    from throughline.adapters.windsurf import WindsurfAdapter
    from throughline.adapters.zed import ZedAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "home", homes["claude_code"])
    monkeypatch.setattr(CodexAdapter, "home", homes["codex"])
    monkeypatch.setattr(ContinueDevAdapter, "home", homes["continue"])
    monkeypatch.setattr(WindsurfAdapter, "home", homes["windsurf"])
    monkeypatch.setattr(ClineAdapter, "home", homes["cline"])
    monkeypatch.setattr(VibeAdapter, "home", homes["vibe"])
    monkeypatch.setattr(CursorAdapter, "home", homes["cursor"])
    monkeypatch.setattr(ZedAdapter, "home", homes["zed"])
    # Hermes is special: its discover() walks ``_hermes_root`` directly.
    monkeypatch.setattr(
        hermes_mod.HermesAdapter,
        "_hermes_root",
        property(lambda self: homes["hermes"]),
    )
    # Cline's discover() looks at hard-coded candidate roots; override that.
    monkeypatch.setattr(
        "throughline.adapters.cline._candidate_task_roots",
        lambda: [homes["cline"]],
    )
    return homes


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_run_many_writes_every_source_end_to_end(tmp_path, db_env, monkeypatch):
    """The writer pulls every present adapter through the full DB cycle.

    Drops synthetic fixtures for Claude Code, Hermes (DB + JSON export),
    and Codex; runs the registry-aware ``run_many``; then asserts the
    expected number of conversations + messages landed, that
    ingestion_log is populated, and that the projects table got
    auto-backfilled with each tool's project name.
    """
    from throughline.adapters import all_adapters
    from throughline.adapters.writer import run_many

    homes = _patch_adapter_homes(monkeypatch, tmp_path)
    _make_claude_code(homes["claude_code"])
    _make_hermes(homes["hermes"])
    _make_codex(homes["codex"])
    _make_cursor(homes["cursor"])
    _make_zed(homes["zed"])
    _make_vibe(homes["vibe"])
    _make_continue(homes["continue"])
    _make_cline(homes["cline"])
    _make_windsurf(homes["windsurf"])

    present = [a for a in all_adapters() if a.is_present()]
    names = {a.name for a in present}
    # Every shipping adapter, not a representative sample. The README claims
    # nine tools; a test covering three of them is not evidence for nine, and
    # the six that were missing are exactly the ones whose on-disk shape nobody
    # had exercised against a real database.
    assert names == set(EXPECTED_MESSAGES), f"setup bug: expected every adapter present, got {sorted(names)}"

    summaries = run_many(present, verbose=False)
    by_name = {s.adapter: s for s in summaries}

    # Per-source expectations. Asserted per adapter rather than as a total, so
    # a failure names the tool that broke instead of a sum that moved.
    for name, expected in EXPECTED_MESSAGES.items():
        summary = by_name[name]
        assert summary.ingested >= 1, f"{name}: nothing ingested"
        assert (
            summary.messages_written >= expected
        ), f"{name}: {summary.messages_written} messages, expected at least {expected}"
        assert summary.errors == 0, f"{name}: {summary.errors} error(s)"

    # DB-side checks: rows actually landed.
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT project_name, COUNT(*) AS n FROM conversations GROUP BY project_name ORDER BY project_name"
            )
            rows = {r["project_name"]: r["n"] for r in cur.fetchall()}
            assert rows.get("repo") == 1  # Claude Code's project_name from cwd
            assert rows.get("hermes", 0) >= 1
            # Codex's project_path comes from cwd "/repo/x" → project_name "x".
            assert rows.get("x") == 1

            cur.execute("SELECT COUNT(*) AS n FROM messages")
            assert cur.fetchone()["n"] >= 6  # 2 cc + 2 hermes-DB + 2 codex (+ JSON)

            # Auto-backfill: projects table got populated.
            cur.execute("SELECT name FROM projects ORDER BY name")
            project_names = {r["name"] for r in cur.fetchall()}
            assert {"repo", "hermes", "x"} <= project_names

            # ingestion_log: every parsed file recorded.
            cur.execute("SELECT COUNT(*) AS n FROM ingestion_log")
            assert cur.fetchone()["n"] >= 3
    finally:
        conn.close()


def test_re_running_is_a_noop_when_nothing_changed(tmp_path, db_env, monkeypatch):
    """Calling ingest twice in a row must not double-write."""
    from throughline.adapters import all_adapters
    from throughline.adapters.writer import run_many

    homes = _patch_adapter_homes(monkeypatch, tmp_path)
    _make_claude_code(homes["claude_code"])

    present = [a for a in all_adapters() if a.is_present()]
    run_many(present, verbose=False)
    # second pass — every file is in ingestion_log with the same hash, so
    # all summaries should report 0 ingested and N skipped.
    summaries2 = run_many(present, verbose=False)
    by_name = {s.adapter: s for s in summaries2}
    assert by_name["claude_code"].ingested == 0
    assert by_name["claude_code"].skipped >= 1
    assert all(s.errors == 0 for s in summaries2)


def test_changed_file_triggers_refresh_not_duplicate(tmp_path, db_env, monkeypatch):
    """When a source file content hash changes, the writer replaces the
    conversation's messages instead of appending duplicates."""
    from throughline.adapters import all_adapters
    from throughline.adapters.writer import run_many

    homes = _patch_adapter_homes(monkeypatch, tmp_path)
    _make_claude_code(homes["claude_code"])

    present = [a for a in all_adapters() if a.is_present()]
    run_many(present, verbose=False)

    # Mutate the JSONL: add a third message.
    f = next(homes["claude_code"].rglob("*.jsonl"))
    new_entry = {
        "type": "user",
        "sessionId": "11111111-1111-1111-1111-111111111111",
        "timestamp": "2026-01-01T10:01:00Z",
        "uuid": "44444444-4444-4444-4444-444444444444",
        "message": {"role": "user", "content": "and one more thing"},
    }
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(new_entry) + "\n")

    run_many(present, verbose=False)

    # Conversations should still be exactly one row (upserted on session_id),
    # but messages should now be 3 — not 5 (which would be the duplicate case).
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM conversations")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM messages")
            assert cur.fetchone()[0] == 3
    finally:
        conn.close()
