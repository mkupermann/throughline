"""End-to-end test: ingest a fake JSONL session and verify DB rows."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _write_session(dir_: Path) -> tuple[Path, str]:
    """Create a minimal Claude Code JSONL session with two user turns and one assistant reply."""
    session_id = str(uuid.uuid4())
    ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    lines = [
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "isSidechain": False,
            "message": {"role": "user", "content": "Hello from integration test"},
            "timestamp": ts,
            "sessionId": session_id,
            "cwd": "/tmp/test-project",
            "entrypoint": "cli",
            "gitBranch": "main",
        },
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "Hi! This is the assistant."}],
            },
            "timestamp": ts,
            "sessionId": session_id,
        },
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "parentUuid": None,
            "isSidechain": False,
            "message": {"role": "user", "content": "Second user message"},
            "timestamp": ts,
            "sessionId": session_id,
        },
    ]
    path = dir_ / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return path, session_id


def test_ingest_session_writes_conversation_and_messages(tmp_path, db_env, db_connection, monkeypatch):
    """
    Point ingest_sessions at a scratch ~/.claude/projects/, run main(), and assert
    that a conversation row plus three messages show up in the test database.
    """
    fake_home = tmp_path / "home"
    projects = fake_home / ".claude" / "projects" / "-tmp-test-project"
    projects.mkdir(parents=True)
    session_path, session_id = _write_session(projects)

    # Redirect Path.home() before importing the module.
    monkeypatch.setenv("HOME", str(fake_home))

    # Fresh import so that module-level Path.home() picks up the patched HOME.
    import importlib
    import sys as _sys

    for mod in ("ingest_sessions",):
        _sys.modules.pop(mod, None)
    ingest_sessions = importlib.import_module("ingest_sessions")
    # Overwrite module-level globals so patched HOME takes effect regardless of import order.
    ingest_sessions.CLAUDE_DIR = fake_home / ".claude"
    ingest_sessions.PROJECTS_DIR = fake_home / ".claude" / "projects"
    ingest_sessions.DB_CONFIG = {
        "dbname": db_env["dbname"],
        "user": db_env["user"],
        "host": db_env["host"],
        "port": db_env["port"],
    }

    ingest_sessions.main()

    with db_connection.cursor() as cur:
        cur.execute("SELECT session_id, message_count FROM conversations WHERE session_id = %s", (session_id,))
        conv = cur.fetchone()
        assert conv is not None, "conversation row was not inserted"
        assert conv[1] == 3

        cur.execute(
            "SELECT count(*) FROM messages m JOIN conversations c ON m.conversation_id = c.id "
            "WHERE c.session_id = %s",
            (session_id,),
        )
        assert cur.fetchone()[0] == 3

        cur.execute(
            "SELECT count(*) FROM ingestion_log WHERE file_path = %s",
            (str(session_path),),
        )
        assert cur.fetchone()[0] == 1


def test_ingest_is_idempotent(tmp_path, db_env, db_connection, monkeypatch):
    """Running ingest twice must not create duplicate conversation/message rows."""
    fake_home = tmp_path / "home"
    projects = fake_home / ".claude" / "projects" / "-tmp-test-project"
    projects.mkdir(parents=True)
    _write_session(projects)

    monkeypatch.setenv("HOME", str(fake_home))

    import importlib
    import sys as _sys

    _sys.modules.pop("ingest_sessions", None)
    ingest_sessions = importlib.import_module("ingest_sessions")
    ingest_sessions.PROJECTS_DIR = fake_home / ".claude" / "projects"
    ingest_sessions.CLAUDE_DIR = fake_home / ".claude"
    ingest_sessions.DB_CONFIG = {
        "dbname": db_env["dbname"],
        "user": db_env["user"],
        "host": db_env["host"],
        "port": db_env["port"],
    }

    ingest_sessions.main()
    ingest_sessions.main()  # should no-op via ingestion_log hash

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM messages")
        assert cur.fetchone()[0] == 3
