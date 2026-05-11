"""Unit tests for throughline.adapters.codex against synthetic JSONL fixtures.

There is no OpenAI Codex CLI installed in the CI environment, so end-to-end
verification isn't possible. These tests pin the parser to the published
session-rollout schema (session_meta + user_message + assistant_message +
tool_call + tool_result events).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline.adapters.codex import CodexAdapter


def _write_rollout(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "rollout-1234567890-abc.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


class TestCodexAdapter:
    def test_name_and_label(self):
        a = CodexAdapter()
        assert a.name == "codex"
        assert "Codex" in a.label

    def test_parse_minimal_session(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "S1", "model": "gpt-5-codex"},
            {"type": "user_message", "content": "list files"},
            {"type": "assistant_message", "content": "Running `ls`..."},
        ]
        path = _write_rollout(tmp_path, events)
        conv = CodexAdapter().parse(path)
        assert conv is not None
        assert conv.model == "gpt-5-codex"
        assert conv.entrypoint == "codex"
        assert conv.project_path == "codex"  # no cwd in session_meta → bucket
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "list files"
        assert conv.messages[1].role == "assistant"
        assert "ls" in conv.messages[1].content

    def test_cwd_is_used_as_project_path(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "S2", "cwd": "/repo/x"},
            {"type": "user_message", "content": "hi"},
        ]
        path = _write_rollout(tmp_path, events)
        conv = CodexAdapter().parse(path)
        assert conv is not None
        assert conv.project_path == "/repo/x"

    def test_tool_call_and_result_become_messages(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "S3"},
            {"type": "user_message", "content": "run ls"},
            {"type": "tool_call", "name": "shell", "arguments": {"command": "ls"}},
            {"type": "tool_result", "name": "shell", "output": "a\nb\n"},
            {"type": "assistant_message", "content": "done"},
        ]
        path = _write_rollout(tmp_path, events)
        conv = CodexAdapter().parse(path)
        assert conv is not None
        roles = [m.role for m in conv.messages]
        # tool_call becomes assistant (it's the model deciding to call something),
        # tool_result becomes the tool_result role.
        assert roles == ["user", "assistant", "tool_result", "assistant"]
        assert conv.messages[1].tool_name == "shell"
        assert conv.messages[1].tool_calls and conv.messages[1].tool_calls[0]["tool_name"] == "shell"
        assert "a\nb" in conv.messages[2].content

    def test_unknown_event_type_is_skipped(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "S4"},
            {"type": "telemetry", "value": 42},
            {"type": "user_message", "content": "hello"},
        ]
        path = _write_rollout(tmp_path, events)
        conv = CodexAdapter().parse(path)
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "user"

    def test_alternate_role_keys(self, tmp_path):
        """Codex schema variants use 'user'/'assistant' as type, not 'user_message'."""
        events = [
            {"type": "session", "id": "S5", "model": "gpt-5-codex"},
            {"type": "user", "content": "hi"},
            {"type": "assistant", "content": "hello", "model": "gpt-5-codex"},
        ]
        path = _write_rollout(tmp_path, events)
        conv = CodexAdapter().parse(path)
        assert conv is not None
        assert [m.role for m in conv.messages] == ["user", "assistant"]

    def test_session_id_is_deterministic_uuid(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "STABLE"},
            {"type": "user_message", "content": "x"},
        ]
        p1 = _write_rollout(tmp_path, events)
        # Same logical session re-parsed from a different file path should
        # still resolve to the same conversations.session_id, so re-ingests
        # update rather than duplicate.
        p2 = tmp_path / "rollout-0000000000-zzz.jsonl"
        p2.write_text(p1.read_text())
        a = CodexAdapter()
        c1 = a.parse(p1)
        c2 = a.parse(p2)
        assert c1.session_id == c2.session_id

    def test_returns_none_on_empty_file(self, tmp_path):
        path = tmp_path / "rollout-empty.jsonl"
        path.write_text("")
        assert CodexAdapter().parse(path) is None

    def test_returns_none_when_no_messages(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "EMPTY"},
            {"type": "telemetry", "n": 1},
        ]
        path = _write_rollout(tmp_path, events)
        assert CodexAdapter().parse(path) is None

    def test_ms_epoch_timestamp_is_parsed(self, tmp_path):
        events = [
            {"type": "session_meta", "session_id": "T1", "started_at": 1734567890123},
            {"type": "user_message", "content": "hello"},
        ]
        path = _write_rollout(tmp_path, events)
        conv = CodexAdapter().parse(path)
        assert conv is not None
        assert conv.started_at.year >= 2024
