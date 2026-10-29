"""Tests for scripts/generate_titles.py — title cleanup and preview building."""

from throughline.jobs import generate_titles as gt


class TestPreviewBuilding:
    def test_includes_user_messages(self):
        messages = [("user", "Hello"), ("assistant", "Hi")]
        preview = gt.build_preview(messages)
        assert "Hello" in preview
        assert "Hi" in preview

    def test_skips_tool_result(self):
        messages = [("user", "Q"), ("tool_result", "OUTPUT"), ("assistant", "A")]
        preview = gt.build_preview(messages)
        assert "OUTPUT" not in preview

    def test_caps_at_max_chars(self):
        messages = [("user", "x" * 10000) for _ in range(20)]
        preview = gt.build_preview(messages)
        assert len(preview) <= gt.MAX_PREVIEW_CHARS

    def test_truncates_individual_messages(self):
        messages = [("user", "y" * 1000)]
        preview = gt.build_preview(messages)
        # Individual message truncated to 500 chars
        assert preview.count("y") <= 500

    def test_empty_content_skipped(self):
        messages = [("user", None), ("user", ""), ("assistant", "actual content")]
        preview = gt.build_preview(messages)
        assert "actual content" in preview

    def test_empty_list_returns_empty(self):
        assert gt.build_preview([]) == ""
