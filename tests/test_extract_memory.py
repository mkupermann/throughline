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

    def test_per_message_cap_is_8000_chars(self):
        # Pin the new cap. The old 1,000-char limit silently beheaded long
        # assistant turns (multi-axis plans, ranked review lists, etc.),
        # so the extractor only ever saw the first paragraph.
        long_content = "x" * 20_000
        messages = [("user", long_content)]
        transcript = em.build_transcript(messages)
        assert em.MAX_MESSAGE_CHARS == 8000
        # 8000 chars + "...[gekürzt]" + role header markup → well under 9 KB.
        assert len(transcript) < 9_000
        assert "gekürzt" in transcript

    def test_messages_at_or_below_cap_are_kept_intact(self):
        # 7,500-char assistant message survives without the gekürzt marker.
        # This is the regression: a 6,533-char "Stunning, innovative, perfect"
        # plan used to be cut to 1,000 chars; it must now pass through whole.
        content = "y" * 7_500
        transcript = em.build_transcript([("assistant", content)])
        assert "gekürzt" not in transcript
        assert "y" * 7_500 in transcript

    def test_truncates_total_transcript(self):
        # Many long messages should still cap at MAX_TRANSCRIPT_CHARS overall.
        messages = [("user", "x" * 7_000) for _ in range(200)]
        transcript = em.build_transcript(messages)
        assert len(transcript) <= em.MAX_TRANSCRIPT_CHARS

    def test_empty_messages_returns_empty(self):
        assert em.build_transcript([]) == ""


class TestExtractorContract:
    """Pin the extractor's prompt and chunk cap so future prompt edits
    can't silently regress the structured-content-preservation guarantee."""

    def test_chunk_cap_is_25(self):
        assert em.MAX_CHUNKS_PER_CONVERSATION == 25

    def test_timeout_is_at_least_300s(self):
        # The original 120 s budget was tight once MAX_MESSAGE_CHARS rose
        # to 8 KB and the chunk cap to 25 — long sessions (200+ messages)
        # started timing out, and since --force-conversations commits the
        # chunk-clear BEFORE the LLM call, a timeout zeroed the row. Pin a
        # floor of 300 s so the timeout/clear race doesn't return.
        assert em.TIMEOUT_PER_CALL >= 300

    def test_prompt_template_substitutes_max_chunks(self):
        # The PROMPT_TEMPLATE uses {MAX_CHUNKS} so the cap is exposed in the
        # rendered prompt — not just enforced as a post-hoc trim. Catches the
        # case where the placeholder is removed and the model goes back to
        # "max 10".
        assert "{MAX_CHUNKS}" in em.PROMPT_TEMPLATE

    def test_prompt_instructs_to_preserve_structured_plans(self):
        # Hermes-style multi-axis plans were being collapsed into generic
        # project_context blurbs. The prompt must tell the extractor to
        # split structured plans into one chunk per ranked item / axis.
        p = em.PROMPT_TEMPLATE.lower()
        assert "axis" in p
        assert "tier" in p
        assert "pro ranked" in p or "pro rankedem item" in p

    def test_parse_id_list_handles_basic_input(self):
        assert em._parse_id_list("10, 15, 47") == [10, 15, 47]

    def test_parse_id_list_skips_blanks(self):
        assert em._parse_id_list("1,,2, ,3") == [1, 2, 3]

    def test_parse_id_list_rejects_non_int(self):
        with pytest.raises(SystemExit):
            em._parse_id_list("1,abc,3")


class TestOllamaBackendSelection:
    def test_pick_excludes_embedding_models(self):
        names = ["nomic-embed-text:latest", "qwen3.6:27b"]
        assert em.pick_ollama_chat_model(names) == "qwen3.6:27b"

    def test_pick_prefers_explicit_when_present(self):
        names = ["nomic-embed-text:latest", "qwen3.6:27b", "llama3.1:8b"]
        assert em.pick_ollama_chat_model(names, preferred="llama3.1:8b") == "llama3.1:8b"

    def test_pick_explicit_absent_falls_back_to_chat(self):
        names = ["nomic-embed-text:latest", "qwen3.6:27b"]
        assert em.pick_ollama_chat_model(names, preferred="not-pulled") == "qwen3.6:27b"

    def test_pick_none_when_only_embeddings(self):
        assert em.pick_ollama_chat_model(["nomic-embed-text:latest"]) is None

    def test_pick_none_when_empty(self):
        assert em.pick_ollama_chat_model([]) is None

    def test_choose_auto_prefers_ollama(self):
        assert em.choose_backend("auto", ollama_available=True, claude_available=True) == "ollama"

    def test_choose_auto_falls_back_to_claude(self):
        assert em.choose_backend("auto", ollama_available=False, claude_available=True) == "claude"

    def test_choose_auto_none_when_nothing(self):
        assert em.choose_backend("auto", ollama_available=False, claude_available=False) is None

    def test_choose_explicit_ollama_requires_ollama(self):
        assert em.choose_backend("ollama", ollama_available=True, claude_available=False) == "ollama"
        assert em.choose_backend("ollama", ollama_available=False, claude_available=True) is None

    def test_choose_explicit_claude_requires_claude(self):
        assert em.choose_backend("claude", ollama_available=True, claude_available=True) == "claude"
        assert em.choose_backend("claude", ollama_available=True, claude_available=False) is None
