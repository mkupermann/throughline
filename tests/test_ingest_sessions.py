"""Tests for scripts/ingest_sessions.py — JSONL parsing, content extraction, role mapping."""

import json
import hashlib
import pytest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "ingest_sessions", ROOT / "scripts" / "ingest_sessions.py"
)
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class TestContentExtraction:
    def test_extract_string_content(self):
        message = {"content": "simple string"}
        assert ingest.extract_content(message) == "simple string"

    def test_extract_list_of_text_blocks(self):
        message = {
            "content": [
                {"type": "text", "text": "First block"},
                {"type": "text", "text": "Second block"},
            ]
        }
        result = ingest.extract_content(message)
        assert "First block" in result
        assert "Second block" in result

    def test_extract_skips_thinking_blocks(self):
        message = {
            "content": [
                {"type": "thinking", "thinking": "internal chain of thought"},
                {"type": "text", "text": "Visible output"},
            ]
        }
        result = ingest.extract_content(message)
        assert "internal chain" not in result
        assert "Visible output" in result

    def test_extract_tool_use_marker(self):
        message = {
            "content": [{"type": "tool_use", "name": "Read", "input": {"file": "x"}}]
        }
        result = ingest.extract_content(message)
        assert "Tool" in result and "Read" in result

    def test_extract_tool_result_truncation(self):
        message = {
            "content": [{"type": "tool_result", "content": "x" * 2000}]
        }
        result = ingest.extract_content(message)
        assert len(result) <= 550  # 500 chars + short prefix

    def test_extract_empty_content(self):
        assert ingest.extract_content({}) == ""

    def test_extract_content_non_dict_in_list(self):
        message = {"content": [{"type": "text", "text": "ok"}, "random-string"]}
        # Should not crash on strings mixed into the list
        result = ingest.extract_content(message)
        assert "ok" in result


class TestToolCallsExtraction:
    def test_extract_single_tool_call(self):
        message = {
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b"}}
            ]
        }
        calls = ingest.extract_tool_calls(message)
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "Read"
        assert calls[0]["input"]["file_path"] == "/a/b"

    def test_extract_multiple_tool_calls(self):
        message = {
            "content": [
                {"type": "tool_use", "name": "Read", "input": {}},
                {"type": "tool_use", "name": "Write", "input": {}},
            ]
        }
        calls = ingest.extract_tool_calls(message)
        assert len(calls) == 2
        assert [c["tool_name"] for c in calls] == ["Read", "Write"]

    def test_no_tool_calls_in_text_only(self):
        message = {"content": [{"type": "text", "text": "no tool"}]}
        assert ingest.extract_tool_calls(message) == []

    def test_no_tool_calls_in_string_content(self):
        message = {"content": "just a string"}
        assert ingest.extract_tool_calls(message) == []


class TestRoleMapping:
    def test_maps_user(self):
        entry = {"type": "user", "message": {"role": "user", "content": "hi"}}
        assert ingest.map_role(entry) == "user"

    def test_maps_assistant(self):
        entry = {"type": "assistant", "message": {"role": "assistant", "content": "hi"}}
        assert ingest.map_role(entry) == "assistant"

    def test_maps_tool_result(self):
        entry = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "output"}],
            },
        }
        assert ingest.map_role(entry) == "tool_result"

    def test_maps_system(self):
        entry = {"type": "system", "message": {"role": "system", "content": "s"}}
        assert ingest.map_role(entry) == "system"


class TestTimestampParsing:
    def test_parses_iso_timestamp(self):
        ts = ingest.parse_timestamp("2026-04-17T10:30:00.000Z")
        assert ts.year == 2026
        assert ts.month == 4
        assert ts.day == 17

    def test_parses_iso_with_offset(self):
        ts = ingest.parse_timestamp("2026-04-17T10:30:00+02:00")
        assert ts.year == 2026

    def test_empty_timestamp_returns_now(self):
        ts = ingest.parse_timestamp("")
        assert ts is not None  # Falls back to datetime.now()

    def test_invalid_timestamp_returns_now(self):
        ts = ingest.parse_timestamp("not-a-date")
        assert ts is not None


class TestFileHashing:
    def test_sha256_deterministic(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("deterministic content")
        h1 = ingest.sha256_file(f)
        h2 = ingest.sha256_file(f)
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_differs_for_different_content(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert ingest.sha256_file(f1) != ingest.sha256_file(f2)


class TestJSONLParsing:
    def test_parses_sample_jsonl(self, tmp_jsonl):
        path, session_id = tmp_jsonl
        entries = []
        with open(path) as f:
            for line in f:
                entries.append(json.loads(line))
        assert len(entries) == 3
        assert entries[0]["sessionId"] == session_id
