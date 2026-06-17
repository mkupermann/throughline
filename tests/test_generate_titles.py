"""Tests for scripts/generate_titles.py.

Covers the pure logic: whole-session preview sampling (beginning + middle +
end), summary cleanup, Ollama chat-model selection, and backend choice
(local Ollama preferred, Claude as fallback). The network/subprocess calls
themselves are exercised live, not here.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "generate_titles", ROOT / "scripts" / "generate_titles.py"
)
gt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gt)


class TestPreviewBuilding:
    def test_includes_user_and_assistant_messages(self):
        messages = [("user", "Hello"), ("assistant", "Hi there")]
        preview = gt.build_preview(messages)
        assert "Hello" in preview
        assert "Hi there" in preview

    def test_skips_tool_result(self):
        messages = [("user", "Q"), ("tool_result", "RAWTOOLOUTPUT"), ("assistant", "A")]
        preview = gt.build_preview(messages)
        assert "RAWTOOLOUTPUT" not in preview

    def test_empty_content_skipped(self):
        messages = [("user", None), ("user", ""), ("assistant", "actual content")]
        preview = gt.build_preview(messages)
        assert "actual content" in preview

    def test_empty_list_returns_empty(self):
        assert gt.build_preview([]) == ""

    def test_per_message_truncation(self):
        messages = [("user", "y" * 5000)]
        preview = gt.build_preview(messages)
        assert preview.count("y") <= gt.MAX_MSG_CHARS

    def test_caps_at_max_chars(self):
        messages = [("user", "x" * 10000) for _ in range(40)]
        preview = gt.build_preview(messages)
        assert len(preview) <= gt.MAX_PREVIEW_CHARS

    def test_long_session_samples_beginning_middle_and_end(self):
        # A session far larger than the budget: the FIRST and LAST turns must
        # survive (so the summary reflects the whole arc, not just the start),
        # and the sampler must mark the omitted middle.
        first = ("user", "ANFANGSTOKEN goal is to set up the data platform")
        bulk = [("assistant", "z" * 1000) for _ in range(60)]
        last = ("assistant", "ENDETOKEN platform is live and the pipeline runs")
        messages = [first, *bulk, last]
        preview = gt.build_preview(messages)
        assert len(preview) <= gt.MAX_PREVIEW_CHARS
        assert "ANFANGSTOKEN" in preview, "beginning of the session was dropped"
        assert "ENDETOKEN" in preview, "end of the session was dropped"
        # The whole transcript can't fit, so an omission marker must appear.
        assert "…" in preview or "..." in preview


class TestCleanSummary:
    def test_strips_surrounding_quotes(self):
        assert gt.clean_summary('"Eine Beschreibung"') == "Eine Beschreibung"
        assert gt.clean_summary("„Eine Beschreibung“") == "Eine Beschreibung"

    def test_collapses_to_single_paragraph(self):
        raw = "Erster Teil.\n\nZweiter Teil.\n  Dritter Teil."
        out = gt.clean_summary(raw)
        assert "\n" not in out
        assert "Erster Teil." in out and "Dritter Teil." in out

    def test_keeps_sentence_punctuation(self):
        out = gt.clean_summary("Ziel war X. Gemacht wurde Y. Ergebnis ist Z.")
        assert out.endswith("Z.")
        assert out.count(".") == 3

    def test_caps_length(self):
        out = gt.clean_summary("Satz. " * 500)
        assert len(out) <= gt.MAX_SUMMARY_CHARS


class TestPickOllamaChatModel:
    def test_excludes_embedding_models(self):
        names = ["nomic-embed-text:latest", "qwen3.6:27b"]
        assert gt.pick_ollama_chat_model(names) == "qwen3.6:27b"

    def test_prefers_explicit_when_present(self):
        names = ["nomic-embed-text:latest", "qwen3.6:27b", "llama3.1:8b"]
        assert gt.pick_ollama_chat_model(names, preferred="llama3.1:8b") == "llama3.1:8b"

    def test_explicit_ignored_when_absent_falls_back_to_chat(self):
        names = ["nomic-embed-text:latest", "qwen3.6:27b"]
        assert gt.pick_ollama_chat_model(names, preferred="not-pulled") == "qwen3.6:27b"

    def test_returns_none_when_only_embeddings(self):
        assert gt.pick_ollama_chat_model(["nomic-embed-text:latest"]) is None

    def test_returns_none_when_empty(self):
        assert gt.pick_ollama_chat_model([]) is None


class TestChooseBackend:
    def test_auto_prefers_ollama_when_available(self):
        assert gt.choose_backend("auto", ollama_available=True, claude_available=True) == "ollama"

    def test_auto_falls_back_to_claude_when_no_ollama(self):
        assert gt.choose_backend("auto", ollama_available=False, claude_available=True) == "claude"

    def test_auto_none_when_nothing_available(self):
        assert gt.choose_backend("auto", ollama_available=False, claude_available=False) is None

    def test_explicit_ollama_requires_ollama(self):
        assert gt.choose_backend("ollama", ollama_available=True, claude_available=False) == "ollama"
        assert gt.choose_backend("ollama", ollama_available=False, claude_available=True) is None

    def test_explicit_claude_requires_claude(self):
        assert gt.choose_backend("claude", ollama_available=True, claude_available=True) == "claude"
        assert gt.choose_backend("claude", ollama_available=True, claude_available=False) is None
