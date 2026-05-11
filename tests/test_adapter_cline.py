"""Unit tests for throughline.adapters.cline against synthetic Cline task dirs.

No real Cline tasks on the test machine, so coverage is via fixture
task directories that mirror the published Cline 3.x storage shape:
api_conversation_history.json + ui_messages.json + task_metadata.json
under a per-task directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from throughline.adapters.cline import ClineAdapter


def _make_task(tmp_path: Path, *, task_id: str, api: list | None = None,
               ui: list | None = None, meta: dict | None = None) -> Path:
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    if api is not None:
        (task_dir / "api_conversation_history.json").write_text(
            json.dumps(api), encoding="utf-8"
        )
    if ui is not None:
        (task_dir / "ui_messages.json").write_text(json.dumps(ui), encoding="utf-8")
    if meta is not None:
        (task_dir / "task_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return task_dir


class TestClineAdapter:
    def test_name_and_label(self):
        a = ClineAdapter()
        assert a.name == "cline"
        assert "Cline" in a.label

    def test_parse_with_api_history(self, tmp_path):
        task = _make_task(
            tmp_path,
            task_id="1700000000000",
            api=[
                {"role": "user", "content": "refactor foo()"},
                {"role": "assistant", "content": "Reading foo.py..."},
            ],
            meta={"id": "1700000000000", "ts": 1700000000000, "task": "refactor foo"},
        )
        conv = ClineAdapter().parse(task)
        assert conv is not None
        assert conv.entrypoint == "cline"
        assert conv.project_path == "cline"
        assert conv.summary == "refactor foo"
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[1].role == "assistant"

    def test_parse_content_blocks_extracted(self, tmp_path):
        task = _make_task(
            tmp_path,
            task_id="t2",
            api=[
                {"role": "user", "content": "run shell ls"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll list files."},
                        {"type": "tool_use", "name": "execute_command",
                         "input": {"command": "ls"}},
                    ],
                },
            ],
        )
        conv = ClineAdapter().parse(task)
        assert conv is not None
        a = conv.messages[1]
        assert "list files" in a.content
        assert "[Tool: execute_command]" in a.content
        assert a.tool_calls and a.tool_calls[0]["tool_name"] == "execute_command"
        assert a.tool_name == "execute_command"

    def test_falls_back_to_ui_messages_when_api_absent(self, tmp_path):
        task = _make_task(
            tmp_path,
            task_id="t3",
            ui=[
                {"ts": 1700000000000, "type": "say", "say": "text",
                 "text": "Working on it..."},
                {"ts": 1700000060000, "type": "ask", "ask": "command",
                 "text": "May I run `rm -rf` ?"},
                {"ts": 1700000070000, "type": "say", "say": "api_req_started",
                 "text": "{\"request\":\"...\"}"},  # noise — should be skipped
            ],
        )
        conv = ClineAdapter().parse(task)
        assert conv is not None
        # api_req_started gets skipped; two real messages survive.
        assert len(conv.messages) == 2
        assert "Working on it" in conv.messages[0].content
        assert "May I run" in conv.messages[1].content

    def test_returns_none_when_no_messages_anywhere(self, tmp_path):
        task = _make_task(tmp_path, task_id="empty")
        assert ClineAdapter().parse(task) is None

    def test_session_id_is_deterministic(self, tmp_path):
        meta = {"id": "stable-id", "ts": 1700000000000, "task": "x"}
        api = [{"role": "user", "content": "hi"}]
        t1 = _make_task(tmp_path / "a", task_id="anything", api=api, meta=meta)
        # Different parent dir, same metadata.id → same conversations.session_id
        t2 = _make_task(tmp_path / "b", task_id="other", api=api, meta=meta)
        a = ClineAdapter()
        assert a.parse(t1).session_id == a.parse(t2).session_id

    def test_sha256_hashes_transcript_file_not_directory(self, tmp_path):
        task = _make_task(
            tmp_path,
            task_id="t4",
            api=[{"role": "user", "content": "v1"}],
        )
        a = ClineAdapter()
        h1 = a.sha256_file(task)
        # Mutate the transcript → hash should change.
        (task / "api_conversation_history.json").write_text(
            json.dumps([{"role": "user", "content": "v2"}]), encoding="utf-8"
        )
        h2 = a.sha256_file(task)
        assert h1 != h2

    def test_discover_walks_multiple_roots(self, tmp_path, monkeypatch):
        # Stub out the candidate roots to point at our tmp_path layout.
        root_a = tmp_path / "roota"
        root_b = tmp_path / "rootb"
        root_a.mkdir(); root_b.mkdir()
        (root_a / "task1").mkdir()
        (root_b / "task2").mkdir()
        monkeypatch.setattr(
            "throughline.adapters.cline._candidate_task_roots",
            lambda: [root_a, root_b, tmp_path / "missing"],
        )
        a = ClineAdapter()
        names = sorted(p.name for p in a.discover())
        assert names == ["task1", "task2"]
