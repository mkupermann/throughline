"""Unit tests for throughline.adapters.zed against synthetic Zed session files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline.adapters.zed import ZedAdapter


def _make_session(tmp_path: Path, *, filename: str = "session_abc123.json", data: dict | None = None) -> Path:
    """Create a synthetic Zed session JSON file."""
    session_path = tmp_path / filename
    session_path.parent.mkdir(parents=True, exist_ok=True)
    if data is not None:
        with open(session_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return session_path


class TestZedAdapter:
    def test_name_and_label(self):
        a = ZedAdapter()
        assert a.name == "zed"
        assert "Zed" in a.label

    def test_home_directory(self):
        a = ZedAdapter()
        assert a.home == Path("~/.zed/data/sessions").expanduser()

    def test_discover_finds_session_files(self, tmp_path, monkeypatch):
        home = tmp_path / "sessions"
        home.mkdir()
        _make_session(home, filename="session_abc123.json", data={"id": "s1", "messages": []})
        _make_session(home, filename="session_def456.json", data={"id": "s2", "messages": []})
        # Create a non-matching file
        (home / "not_a_session.txt").write_text("not a session")
        
        a = ZedAdapter()
        monkeypatch.setattr(a, "home", home)
        
        discovered = sorted(p.name for p in a.discover())
        assert discovered == ["session_abc123.json", "session_def456.json"]

    def test_discover_returns_empty_when_home_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent"
        a = ZedAdapter()
        monkeypatch.setattr(a, "home", missing_dir)
        assert list(a.discover()) == []

    def test_parse_basic_session(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_basic.json",
            data={
                "id": "session_abc123",
                "messages": [
                    {"role": "user", "content": "Hello, Zed!", "id": "msg_1", "timestamp": 1700000000000},
                    {"role": "assistant", "content": "How can I help?", "id": "msg_2", "timestamp": 1700000001000},
                ],
                "started_at": 1700000000000,
                "ended_at": 1700000002000,
                "model": "zed-pro",
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert conv.entrypoint == "zed"
        assert conv.model == "zed-pro"
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello, Zed!"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "How can I help?"

    def test_parse_with_code_blocks(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_code.json",
            data={
                "id": "session_code",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Here's the fix:"},
                            {"type": "code", "language": "python", "text": "def fix():\n    return 42"},
                        ],
                        "id": "msg_1",
                        "timestamp": 1700000000000,
                    },
                ],
                "started_at": 1700000000000,
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert len(conv.messages) == 1
        content = conv.messages[0].content
        assert "Here's the fix:" in content
        assert "def fix():" in content
        assert "return 42" in content

    def test_parse_with_tool_calls(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_tools.json",
            data={
                "id": "session_tools",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Running command",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "bash",
                                    "arguments": {"command": "echo hello"},
                                }
                            }
                        ],
                        "id": "msg_1",
                        "timestamp": 1700000000000,
                    },
                ],
                "started_at": 1700000000000,
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert len(conv.messages) == 1
        msg = conv.messages[0]
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["tool_name"] == "bash"

    def test_parse_with_workspace(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_workspace.json",
            data={
                "id": "session_workspace",
                "messages": [
                    {"role": "user", "content": "test", "id": "msg_1", "timestamp": 1700000000000},
                ],
                "started_at": 1700000000000,
                "workspace": "/Users/me/my-project",
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert conv.project_path == "my-project"

    def test_parse_with_token_counts(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_tokens.json",
            data={
                "id": "session_tokens",
                "messages": [
                    {"role": "user", "content": "test", "id": "msg_1", "timestamp": 1700000000000},
                ],
                "started_at": 1700000000000,
                "token_count_in": 100,
                "token_count_out": 200,
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert conv.token_count_in == 100
        assert conv.token_count_out == 200

    def test_parse_with_summary(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_summary.json",
            data={
                "id": "session_summary",
                "messages": [
                    {"role": "user", "content": "test", "id": "msg_1", "timestamp": 1700000000000},
                ],
                "started_at": 1700000000000,
                "title": "Fix the authentication bug",
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert conv.summary == "Fix the authentication bug"

    def test_parse_returns_none_for_empty_messages(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_empty.json",
            data={"id": "session_empty", "messages": [], "started_at": 1700000000000},
        )
        
        assert ZedAdapter().parse(session_path) is None

    def test_parse_returns_none_for_missing_id(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_no_id.json",
            data={"messages": [], "started_at": 1700000000000},
        )
        
        assert ZedAdapter().parse(session_path) is None

    def test_parse_returns_none_for_non_file(self, tmp_path):
        not_a_file = tmp_path / "not_a_file"
        
        assert ZedAdapter().parse(not_a_file) is None

    def test_session_id_is_deterministic(self, tmp_path):
        data = {
            "id": "test_session_123",
            "messages": [{"role": "user", "content": "test", "id": "msg_1", "timestamp": 1700000000000}],
            "started_at": 1700000000000,
        }
        
        session_path1 = _make_session(tmp_path / "a", filename="session_1.json", data=data)
        session_path2 = _make_session(tmp_path / "b", filename="session_2.json", data=data)
        
        conv1 = ZedAdapter().parse(session_path1)
        conv2 = ZedAdapter().parse(session_path2)
        
        assert conv1 is not None
        assert conv2 is not None
        # Same session ID in data should produce same UUID
        assert conv1.session_id == conv2.session_id

    def test_metadata_preservation(self, tmp_path):
        session_path = _make_session(
            tmp_path,
            filename="session_metadata.json",
            data={
                "id": "session_meta",
                "messages": [{"role": "user", "content": "test", "id": "msg_1", "timestamp": 1700000000000}],
                "started_at": 1700000000000,
                "version": "1.0.0",
                "workspace": "/path/to/workspace",
            },
        )
        
        conv = ZedAdapter().parse(session_path)
        assert conv is not None
        assert conv.metadata["source"] == "zed"
        assert conv.metadata["zed_session_id"] == "session_meta"
        assert conv.metadata["zed_version"] == "1.0.0"
        assert conv.metadata["workspace"] == "/path/to/workspace"

    def test_is_present_when_dir_exists_but_empty(self, tmp_path, monkeypatch):
        # Spec §4.4: an existing-but-empty data dir must not report present.
        zed_dir = tmp_path / ".zed" / "data" / "sessions"
        zed_dir.mkdir(parents=True)
        a = ZedAdapter()
        monkeypatch.setattr(a, "home", zed_dir)
        assert a.is_present() is False

    def test_is_present_when_a_session_file_exists(self, tmp_path, monkeypatch):
        zed_dir = tmp_path / ".zed" / "data" / "sessions"
        zed_dir.mkdir(parents=True)
        (zed_dir / "session_1.json").write_text("{}\n")
        a = ZedAdapter()
        monkeypatch.setattr(a, "home", zed_dir)
        assert a.is_present() is True

    def test_is_present_when_dir_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent"
        a = ZedAdapter()
        monkeypatch.setattr(a, "home", missing_dir)
        assert a.is_present() is False
