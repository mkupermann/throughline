"""Unit tests for scripts/ingest_hermes.py — parsing + role/content mapping.

Pure-Python tests; no DB connection required. The DB write paths are
covered by integration tests against a real Postgres.
"""

from datetime import datetime, timezone

import pytest

from throughline.jobs import ingest_hermes as ih


class TestRoleMapping:
    def test_maps_user(self):
        assert ih._ROLE_MAP["user"] == "user"

    def test_maps_assistant(self):
        assert ih._ROLE_MAP["assistant"] == "assistant"

    def test_maps_system(self):
        assert ih._ROLE_MAP["system"] == "system"

    def test_maps_tool_to_tool_result(self):
        assert ih._ROLE_MAP["tool"] == "tool_result"

    def test_maps_function_to_tool_result(self):
        # Some OpenAI-style frameworks use "function" instead of "tool".
        assert ih._ROLE_MAP["function"] == "tool_result"

    def test_unknown_role_absent(self):
        assert "image" not in ih._ROLE_MAP


class TestContentNormalisation:
    def test_string_content_passes_through(self):
        text, blocks = ih._normalise_content("hello world")
        assert text == "hello world"
        assert blocks is None

    def test_list_of_text_blocks_concatenates(self):
        content = [
            {"type": "text", "text": "alpha"},
            {"type": "text", "text": "beta"},
        ]
        text, blocks = ih._normalise_content(content)
        assert "alpha" in text and "beta" in text
        assert blocks == content

    def test_tool_use_block_emits_marker(self):
        content = [{"type": "tool_use", "name": "bash"}]
        text, _ = ih._normalise_content(content)
        assert "[Tool: bash]" in text

    def test_tool_result_block_truncates_long_content(self):
        long = "x" * 1000
        content = [{"type": "tool_result", "content": long}]
        text, _ = ih._normalise_content(content)
        # Truncation cap is 500 chars in _normalise_content.
        assert len(text) <= 500

    def test_none_content_returns_empty_string(self):
        text, blocks = ih._normalise_content(None)
        assert text == ""
        assert blocks is None


class TestMessageMetadata:
    def test_lifts_reasoning_fields(self):
        msg = {
            "role": "assistant",
            "content": "x",
            "reasoning": "step 1",
            "reasoning_content": "longer trace",
            "finish_reason": "stop",
        }
        md = ih._message_metadata(msg)
        assert md["reasoning"] == "step 1"
        assert md["reasoning_content"] == "longer trace"
        assert md["finish_reason"] == "stop"

    def test_skips_absent_keys(self):
        md = ih._message_metadata({"role": "user", "content": "hi"})
        assert md == {}

    def test_drops_explicit_none_values(self):
        md = ih._message_metadata({"reasoning": None, "finish_reason": "stop"})
        assert "reasoning" not in md
        assert md["finish_reason"] == "stop"


class TestUuidDerivation:
    def test_session_uuid_is_deterministic(self):
        a = ih._session_uuid("20260511_160653_6f89a7")
        b = ih._session_uuid("20260511_160653_6f89a7")
        assert a == b

    def test_session_uuid_changes_with_id(self):
        a = ih._session_uuid("sess-A")
        b = ih._session_uuid("sess-B")
        assert a != b

    def test_message_uuid_is_indexed(self):
        sid = "20260511_160653_6f89a7"
        u0 = ih._message_uuid(sid, 0)
        u1 = ih._message_uuid(sid, 1)
        assert u0 != u1
        # Same index re-derives identically.
        assert u0 == ih._message_uuid(sid, 0)


class TestTimestampParsing:
    def test_parses_naive_iso_as_utc(self):
        dt = ih.parse_timestamp("2026-05-11T16:07:09.607187")
        assert dt.tzinfo is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 11

    def test_parses_offset_iso(self):
        dt = ih.parse_timestamp("2026-05-11T16:07:09+00:00")
        assert dt.tzinfo is not None

    def test_empty_returns_now_with_tz(self):
        before = datetime.now(timezone.utc)
        dt = ih.parse_timestamp("")
        assert dt.tzinfo is not None
        assert dt >= before

    def test_garbage_returns_now_with_tz(self):
        dt = ih.parse_timestamp("not-a-date")
        assert dt.tzinfo is not None


class TestFileHashing:
    def test_sha256_deterministic(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_bytes(b'{"hi": "there"}')
        assert ih.sha256_file(p) == ih.sha256_file(p)

    def test_sha256_changes_with_content(self, tmp_path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        a.write_bytes(b'{"v": 1}')
        b.write_bytes(b'{"v": 2}')
        assert ih.sha256_file(a) != ih.sha256_file(b)
