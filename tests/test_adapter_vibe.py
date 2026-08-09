"""Unit tests for throughline.adapters.vibe against synthetic Vibe session dirs.

No real Vibe sessions on the test machine, so coverage is via fixture
session directories that mirror the Vibe storage shape:
meta.json + messages.jsonl under a per-session directory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from throughline.adapters.vibe import VibeAdapter


def _make_session(
    tmp_path: Path,
    *,
    session_id: str = "session_20260727_180500_c919513d",
    meta: dict | None = None,
    messages: list | None = None,
) -> Path:
    """Create a synthetic Vibe session directory with meta.json and messages.jsonl."""
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    if meta is not None:
        (session_dir / "meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
    
    if messages is not None:
        messages_path = session_dir / "messages.jsonl"
        with open(messages_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
    
    return session_dir


class TestVibeAdapter:
    def test_name_and_label(self):
        a = VibeAdapter()
        assert a.name == "vibe"
        assert "Vibe" in a.label
        assert "Mistral" in a.label

    def test_home_directory(self):
        a = VibeAdapter()
        assert a.home == Path("~/.vibe/logs/session").expanduser()

    def test_is_present_when_dir_exists(self, tmp_path, monkeypatch):
        # Create a fake vibe directory
        vibe_dir = tmp_path / ".vibe" / "logs" / "session"
        vibe_dir.mkdir(parents=True)
        a = VibeAdapter()
        monkeypatch.setattr(a, "home", vibe_dir)
        assert a.is_present() is True

    def test_is_present_when_dir_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent"
        a = VibeAdapter()
        monkeypatch.setattr(a, "home", missing_dir)
        assert a.is_present() is False

    def test_discover_finds_matching_session_dirs(self, tmp_path, monkeypatch):
        # Create fake session directories
        home = tmp_path / "sessions"
        home.mkdir()
        _make_session(home, session_id="session_20260727_180500_c919513d")
        _make_session(home, session_id="session_20260728_091450_c0d81646")
        # Create a non-matching directory
        (home / "not_a_session").mkdir()
        
        a = VibeAdapter()
        monkeypatch.setattr(a, "home", home)
        
        discovered = sorted(p.name for p in a.discover())
        assert discovered == ["session_20260727_180500_c919513d", "session_20260728_091450_c0d81646"]

    def test_discover_returns_empty_when_home_missing(self, tmp_path, monkeypatch):
        missing_dir = tmp_path / "nonexistent"
        a = VibeAdapter()
        monkeypatch.setattr(a, "home", missing_dir)
        assert list(a.discover()) == []

    def test_parse_basic_session(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_c919513d",
            meta={
                "session_id": "session_20260727_180500_c919513d",
                "start_time": "2026-07-27T18:05:00Z",
                "end_time": "2026-07-27T18:10:00Z",
                "environment": {"working_directory": "/path/to/project"},
                "model": "mistral-large",
                "stats": {
                    "session_prompt_tokens": 100,
                    "session_completion_tokens": 200,
                },
                "title": "Test session",
            },
            messages=[
                {"role": "user", "content": "Hello, how are you?", "message_id": "msg_1", "timestamp": "2026-07-27T18:05:01Z"},
                {"role": "assistant", "content": "I'm doing well, thanks!", "message_id": "msg_2", "timestamp": "2026-07-27T18:05:02Z"},
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert conv.entrypoint == ""
        assert conv.project_path == "project"
        assert conv.model == "mistral-large"
        assert conv.summary == "Test session"
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello, how are you?"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "I'm doing well, thanks!"
        assert conv.token_count_in == 100
        assert conv.token_count_out == 200

    def test_parse_with_ansi_codes(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_test",
            meta={
                "session_id": "session_20260727_180500_test",
                "start_time": "2026-07-27T18:05:00Z",
                "environment": {"working_directory": "/path/to/ansitest"},
                "model": "mistral-small",
                "stats": {},
            },
            messages=[
                {
                    "role": "assistant",
                    "content": "\x1b[32mThis is green text\x1b[0m and \x1b[1mbold\x1b[0m",
                    "message_id": "msg_1",
                    "timestamp": "2026-07-27T18:05:01Z",
                },
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert len(conv.messages) == 1
        # ANSI codes should be stripped
        assert "\x1b[" not in conv.messages[0].content
        assert "This is green text" in conv.messages[0].content
        assert "bold" in conv.messages[0].content

    def test_parse_with_tool_calls(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_tools",
            meta={
                "session_id": "session_20260727_180500_tools",
                "start_time": "2026-07-27T18:05:00Z",
                "environment": {"working_directory": "/path/to/tools"},
                "model": "mistral-large",
                "stats": {},
            },
            messages=[
                {
                    "role": "assistant",
                    "content": "I'll run a command",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "bash",
                                "arguments": {"command": "ls -la"},
                            }
                        }
                    ],
                    "message_id": "msg_1",
                    "timestamp": "2026-07-27T18:05:01Z",
                },
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert len(conv.messages) == 1
        msg = conv.messages[0]
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["tool_name"] == "bash"
        assert msg.tool_name == "bash"

    def test_parse_with_reasoning_content(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_reason",
            meta={
                "session_id": "session_20260727_180500_reason",
                "start_time": "2026-07-27T18:05:00Z",
                "environment": {"working_directory": "/path/to/reason"},
                "model": "mistral-large",
                "stats": {},
            },
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "Let me think about this...",
                    "message_id": "msg_1",
                    "timestamp": "2026-07-27T18:05:01Z",
                },
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert len(conv.messages) == 1
        # reasoning_content should be used when content is empty
        assert "Let me think about this..." in conv.messages[0].content

    def test_parse_system_role(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_system",
            meta={
                "session_id": "session_20260727_180500_system",
                "start_time": "2026-07-27T18:05:00Z",
                "environment": {"working_directory": "/path/to/sys"},
                "model": "mistral-large",
                "stats": {},
            },
            messages=[
                {"role": "system", "content": "You are a helpful assistant", "message_id": "msg_1", "timestamp": "2026-07-27T18:05:01Z"},
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "system"

    def test_parse_tool_role(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_toolmsg",
            meta={
                "session_id": "session_20260727_180500_toolmsg",
                "start_time": "2026-07-27T18:05:00Z",
                "environment": {"working_directory": "/path/to/tool"},
                "model": "mistral-large",
                "stats": {},
            },
            messages=[
                {"role": "tool", "content": "Tool output here", "message_id": "msg_1", "timestamp": "2026-07-27T18:05:01Z"},
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "tool_result"

    def test_parse_returns_none_when_no_meta(self, tmp_path):
        session_dir = tmp_path / "session_20260727_180500_nometa"
        session_dir.mkdir()
        # No meta.json, only messages
        (session_dir / "messages.jsonl").write_text('{"role": "user", "content": "hi"}\n')
        
        assert VibeAdapter().parse(session_dir) is None

    def test_parse_returns_none_when_no_messages(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_nomsgs",
            meta={"session_id": "session_20260727_180500_nomsgs"},
            messages=None,
        )
        
        assert VibeAdapter().parse(session_dir) is None

    def test_parse_returns_none_for_non_directory(self, tmp_path):
        file_path = tmp_path / "not_a_directory.txt"
        file_path.write_text("not a session")
        
        assert VibeAdapter().parse(file_path) is None

    def test_session_id_is_deterministic(self, tmp_path):
        # Same meta session_id should produce same conversation session_id
        meta = {"session_id": "test_session_123", "start_time": "2026-07-27T18:05:00Z"}
        messages = [{"role": "user", "content": "hi", "message_id": "msg_1", "timestamp": "2026-07-27T18:05:01Z"}]
        
        session_dir1 = _make_session(tmp_path / "a", session_id="session_20260727_180500_abc", meta=meta, messages=messages)
        session_dir2 = _make_session(tmp_path / "b", session_id="session_20260727_180500_xyz", meta=meta, messages=messages)
        
        conv1 = VibeAdapter().parse(session_dir1)
        conv2 = VibeAdapter().parse(session_dir2)
        assert conv1 is not None
        assert conv2 is not None
        # Same meta.session_id -> same derived UUID
        assert conv1.session_id == conv2.session_id
        
        # Different meta session_id should produce different conversation session_id
        meta2 = {"session_id": "different_session_456", "start_time": "2026-07-27T18:05:00Z"}
        session_dir3 = _make_session(tmp_path / "c", session_id="session_20260727_180500_def", meta=meta2, messages=messages)
        conv3 = VibeAdapter().parse(session_dir3)
        assert conv3 is not None
        assert conv3.session_id != conv1.session_id

    def test_discover_walks_multiple_roots(self, tmp_path, monkeypatch):
        # Test that discover correctly identifies session directories
        root_a = tmp_path / "roota"
        root_b = tmp_path / "rootb"
        root_a.mkdir(); root_b.mkdir()
        
        _make_session(root_a, session_id="session_20260727_180500_aaa")
        _make_session(root_b, session_id="session_20260727_180500_bbb")
        # Non-matching directory
        (root_a / "not_a_session").mkdir()
        
        a = VibeAdapter()
        monkeypatch.setattr(a, "home", root_a)
        names = sorted(p.name for p in a.discover())
        assert names == ["session_20260727_180500_aaa"]

    def test_metadata_preservation(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_meta",
            meta={
                "session_id": "session_20260727_180500_meta",
                "start_time": "2026-07-27T18:05:00Z",
                "end_time": "2026-07-27T18:10:00Z",
                "environment": {"working_directory": "/path/to/meta"},
                "model": "mistral-large",
                "stats": {"session_prompt_tokens": 100, "session_completion_tokens": 200},
                "title": "Metadata test",
                "title_source": "user",
                "git_commit": "abc123",
                "git_branch": "main",
                "username": "testuser",
                "parent_session_id": "parent_123",
                "tools_available": ["bash", "grep"],
            },
            messages=[
                {"role": "user", "content": "test", "message_id": "msg_1", "timestamp": "2026-07-27T18:05:01Z"},
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert conv.metadata["source"] == "vibe"
        assert conv.metadata["vibe_session_id"] == "session_20260727_180500_meta"
        assert conv.metadata["parent_session_id"] == "parent_123"
        assert conv.metadata["username"] == "testuser"
        assert conv.metadata["title"] == "Metadata test"
        assert conv.metadata["git_commit"] == "abc123"
        assert conv.metadata["git_branch"] == "main"
        assert conv.metadata["tools_available_count"] == 2

    def test_injected_message_handling(self, tmp_path):
        session_dir = _make_session(
            tmp_path,
            session_id="session_20260727_180500_injected",
            meta={
                "session_id": "session_20260727_180500_injected",
                "start_time": "2026-07-27T18:05:00Z",
                "environment": {"working_directory": "/path/to/inject"},
                "model": "mistral-large",
                "stats": {},
            },
            messages=[
                {
                    "role": "user",
                    "content": "Original message",
                    "message_id": "msg_1",
                    "timestamp": "2026-07-27T18:05:01Z",
                    "injected": False,
                },
                {
                    "role": "assistant",
                    "content": "Injected context",
                    "message_id": "msg_2",
                    "timestamp": "2026-07-27T18:05:02Z",
                    "injected": True,
                    "reasoning_message_id": "reason_1",
                    "tool_call_id": "tool_1",
                },
            ],
        )
        
        conv = VibeAdapter().parse(session_dir)
        assert conv is not None
        assert len(conv.messages) == 2
        assert conv.messages[0].is_sidechain is False
        assert conv.messages[1].is_sidechain is True
        assert conv.messages[1].metadata.get("injected") is True
        assert conv.messages[1].metadata.get("reasoning_message_id") == "reason_1"
