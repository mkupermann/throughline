"""Tests for scripts/extract_memory.py — JSON response parsing and transcript building."""

import pytest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "extract_memory", ROOT / "scripts" / "extract_memory.py"
)
em = importlib.util.module_from_spec(spec)
spec.loader.exec_module(em)


class TestJSONResponseParsing:
    def test_parses_clean_json_array(self):
        text = '[{"content": "A decision", "category": "decision", "tags": ["x"], "confidence": 0.9, "project": "p1"}]'
        result = em.parse_json_response(text)
        assert len(result) == 1
        assert result[0]["category"] == "decision"

    def test_strips_markdown_fences(self):
        text = '```json\n[{"content": "x", "category": "insight"}]\n```'
        result = em.parse_json_response(text)
        assert len(result) == 1

    def test_strips_plain_fences(self):
        text = '```\n[{"content": "x", "category": "pattern"}]\n```'
        result = em.parse_json_response(text)
        assert len(result) == 1

    def test_empty_array(self):
        assert em.parse_json_response("[]") == []

    def test_malformed_json_returns_empty(self):
        assert em.parse_json_response("{broken") == []

    def test_no_array_returns_empty(self):
        assert em.parse_json_response("just prose, no JSON") == []

    def test_finds_array_in_surrounding_text(self):
        text = 'Sure, here are the chunks: [{"content": "c", "category": "insight"}] — done.'
        result = em.parse_json_response(text)
        assert len(result) == 1


class TestTranscriptBuilding:
    def test_includes_user_and_assistant(self):
        messages = [
            ("user", "Question?"),
            ("assistant", "Answer."),
        ]
        transcript = em.build_transcript(messages)
        assert "Question?" in transcript
        assert "Answer." in transcript
        assert "USER" in transcript or "user" in transcript.upper()

    def test_skips_tool_result(self):
        messages = [
            ("user", "Q"),
            ("tool_result", "should be skipped"),
            ("assistant", "A"),
        ]
        transcript = em.build_transcript(messages)
        assert "should be skipped" not in transcript

    def test_truncates_individual_messages(self):
        long_content = "x" * 2000
        messages = [("user", long_content)]
        transcript = em.build_transcript(messages)
        # Each message gets truncated to 1000 chars + marker
        assert len(transcript) < 1200

    def test_truncates_total_transcript(self):
        # Many long messages should cap at MAX_TRANSCRIPT_CHARS
        messages = [("user", "x" * 900) for _ in range(200)]
        transcript = em.build_transcript(messages)
        assert len(transcript) <= em.MAX_TRANSCRIPT_CHARS

    def test_empty_messages_returns_empty(self):
        assert em.build_transcript([]) == ""
