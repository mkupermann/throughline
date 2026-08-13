"""Unit tests for throughline.adapters.cursor against synthetic Cursor session files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline.adapters.cursor import CursorAdapter


def _make_session(tmp_path: Path, *, filename: str = "session_abc123.jsonl", messages: list | None = None) -> Path:
    """Create a synthetic Cursor session JSONL file."""
    session_path = tmp_path / filename
    if messages is not None:
        with open(session_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
    return session_path


class TestCursorAdapter:
    def test_name_and_label(self):
        a = CursorAdapter()
        assert a.name == "cursor"
        assert "Cursor" in a.label

    def test_home_directory(self):
        a = CursorAdapter()
        assert a.home == Path("~/.cursor/sessions").expanduser()

    def test_discover_finds_jsonl_files(self, tmp_path, monkeypatch):
        home = tmp_path / "sessions"
        home.mkdir()
        _make_session(home, filename="session_abc.jsonl", messages=[{"role": "user", "content": "test"}])
        _make_session(home, filename="session_def.jsonl", messages=[{"role": "user", "content": "test"}])
        # Create a non-matching file
        (home / "not_a_session.txt").write_text("not a session")
        
        a = CursorAdapter()
        monkeypatch.setattr(a, "home", home)
        
        discovered = sorted(p.name for p in a.discover())
        assert discovered == ["session_abc.jsonl", "session_def.jsonl"]

    def test_discover_returns_empty_when_home_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent"
        a = CursorAdapter()
        monkeypatch.setattr(a, "home", missing_dir)
        assert list(a.discover()) == []

    def test_parse_basic_session(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_abc.jsonl",
            messages=[
                {"role": "user", "content": "Hello, Cursor!", "message_id": "msg_1"},
                {"role": "assistant", "content": "How can I help?", "message_id": "msg_2"},
            ],
        )
        
        conv = CursorAdapter().parse(session_path)
        assert conv is not None
        assert conv.entrypoint == "cursor"
        assert conv.model is None
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello, Cursor!"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "How can I help?"

    def test_parse_with_tool_calls(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_tools.jsonl",
            messages=[
                {
                    "role": "assistant",
                    "content": "I'll run this command",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "execute_command",
                                "arguments": {"command": "ls -la"},
                            }
                        }
                    ],
                    "message_id": "msg_1",
                },
            ],
        )
        
        conv = CursorAdapter().parse(session_path)
        assert conv is not None
        assert len(conv.messages) == 1
        msg = conv.messages[0]
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["tool_name"] == "execute_command"
        assert msg.tool_name == "execute_command"

    def test_parse_with_content_blocks(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_blocks.jsonl",
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Here's some code:"},
                        {"type": "code", "language": "python", "text": "def hello():\n    pass"},
                    ],
                    "message_id": "msg_1",
                },
            ],
        )
        
        conv = CursorAdapter().parse(session_path)
        assert conv is not None
        assert len(conv.messages) == 1
        content = conv.messages[0].content
        assert "Here's some code:" in content
        # Code blocks are formatted with markdown
        assert "def hello():" in content or "```" in content

    def test_parse_returns_none_for_empty_file(self, tmp_path):
        session_path = _make_session(tmp_path, filename="empty.jsonl", messages=[])
        
        assert CursorAdapter().parse(session_path) is None

    def test_parse_returns_none_for_non_file(self, tmp_path):
        not_a_file = tmp_path / "not_a_file"
        
        assert CursorAdapter().parse(not_a_file) is None

    def test_session_id_is_deterministic(self, tmp_path):
        messages = [{"role": "user", "content": "test", "message_id": "msg_1"}]
        
        session_dir1 = tmp_path / "a"
        session_dir2 = tmp_path / "b"
        session_dir1.mkdir()
        session_dir2.mkdir()
        
        session_path1 = _make_session(session_dir1, filename="session_abc.jsonl", messages=messages)
        session_path2 = _make_session(session_dir2, filename="session_xyz.jsonl", messages=messages)
        
        conv1 = CursorAdapter().parse(session_path1)
        conv2 = CursorAdapter().parse(session_path2)
        
        assert conv1 is not None
        assert conv2 is not None
        # Different file names but same content should have different session IDs
        # (because the file name is part of the identity in the current implementation)
        assert conv1.session_id != conv2.session_id

    def test_metadata_preservation(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_meta.jsonl",
            messages=[
                {
                    "role": "user",
                    "content": "test",
                    "message_id": "msg_1",
                    "session_hash": "test_hash_123",
                },
            ],
        )
        
        conv = CursorAdapter().parse(session_path)
        assert conv is not None
        assert conv.metadata["source"] == "cursor"
        assert "cursor_session_hash" in conv.metadata or "file_path" in conv.metadata

    def test_is_present_when_dir_exists_but_empty(self, tmp_path, monkeypatch):
        # Spec §4.4: an existing-but-empty data dir must not report present.
        cursor_dir = tmp_path / ".cursor" / "sessions"
        cursor_dir.mkdir(parents=True)
        a = CursorAdapter()
        monkeypatch.setattr(a, "home", cursor_dir)
        assert a.is_present() is False

    def test_is_present_when_a_session_file_exists(self, tmp_path, monkeypatch):
        cursor_dir = tmp_path / ".cursor" / "sessions"
        cursor_dir.mkdir(parents=True)
        (cursor_dir / "session_1.jsonl").write_text("{}\n")
        a = CursorAdapter()
        monkeypatch.setattr(a, "home", cursor_dir)
        assert a.is_present() is True

    def test_is_present_when_dir_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent"
        a = CursorAdapter()
        monkeypatch.setattr(a, "home", missing_dir)
        assert a.is_present() is False


class TestMessageIdsSurviveTheDatabase:
    """`messages.uuid` is a uuid column; Cursor's message ids are not uuids.

    The adapter passed `message_id` through verbatim, so Postgres rejected the
    insert and the writer dropped the entire session — not a partial import, no
    import at all. Every other adapter already derived a UUID5; these did not,
    and no unit test noticed because parsing never touches a database.
    """

    def test_a_non_uuid_message_id_becomes_a_uuid(self, tmp_path):
        import uuid as _uuid

        _make_session(
            tmp_path,
            filename="session_x.jsonl",
            messages=[{"role": "user", "content": "hi", "message_id": "msg_1"}],
        )
        conv = CursorAdapter().parse(tmp_path / "session_x.jsonl")
        assert conv is not None
        _uuid.UUID(conv.messages[0].uuid)  # raises if the column would reject it

    def test_the_derived_id_is_stable_across_parses(self, tmp_path):
        """The writer's idempotency depends on it: a second ingest of an
        unchanged file must produce the same uuid, or every run duplicates."""
        _make_session(
            tmp_path,
            filename="session_y.jsonl",
            messages=[{"role": "user", "content": "hi", "message_id": "msg_1"}],
        )
        first = CursorAdapter().parse(tmp_path / "session_y.jsonl")
        second = CursorAdapter().parse(tmp_path / "session_y.jsonl")
        assert first.messages[0].uuid == second.messages[0].uuid

    def test_a_real_uuid_is_kept_as_is(self, tmp_path):
        """Tools that do supply proper ids keep them — no gratuitous rewriting."""
        real = "6f1c2f9e-4a3b-4c5d-8e9f-0a1b2c3d4e5f"
        _make_session(
            tmp_path,
            filename="session_z.jsonl",
            messages=[{"role": "user", "content": "hi", "message_id": real}],
        )
        conv = CursorAdapter().parse(tmp_path / "session_z.jsonl")
        assert conv.messages[0].uuid == real
