"""Unit tests for throughline.adapters.windsurf against synthetic plan files.

Windsurf plans are single Markdown files; the adapter materialises each
as a one-message conversation keyed by the file's URL-namespace uuid5.
"""

from __future__ import annotations

from throughline.adapters.windsurf import WindsurfAdapter


class TestWindsurfAdapter:
    def test_name_and_label(self):
        a = WindsurfAdapter()
        assert a.name == "windsurf"
        assert "Windsurf" in a.label

    def test_parse_with_h1_title(self, tmp_path):
        p = tmp_path / "plan-abc123.md"
        p.write_text(
            "# Ship the new auth flow\n\n"
            "We need to migrate token storage off localStorage. Steps:\n"
            "1. Spike the new endpoint.\n"
            "2. Wire the cookie path.\n",
            encoding="utf-8",
        )
        conv = WindsurfAdapter().parse(p)
        assert conv is not None
        assert conv.summary == "Ship the new auth flow"
        assert conv.project_path == "windsurf"
        assert conv.entrypoint == "windsurf"
        assert conv.model == "windsurf-cascade"
        assert len(conv.messages) == 1
        assert conv.messages[0].role == "user"
        assert "localStorage" in conv.messages[0].content

    def test_parse_falls_back_to_stem_title(self, tmp_path):
        # No H1 → derive a title from the filename, stripping the 6-hex suffix.
        p = tmp_path / "migrate-auth-flow-a1b2c3.md"
        p.write_text(
            "No leading heading here.\n\nBut still enough body text to satisfy the 50-char minimum.\n",
            encoding="utf-8",
        )
        conv = WindsurfAdapter().parse(p)
        assert conv is not None
        assert conv.summary == "Migrate Auth Flow"

    def test_short_files_are_skipped(self, tmp_path):
        # < 50 chars body → no conversation.
        p = tmp_path / "tiny.md"
        p.write_text("# Tiny\n", encoding="utf-8")
        assert WindsurfAdapter().parse(p) is None

    def test_unreadable_file_returns_none(self, tmp_path):
        p = tmp_path / "nope.md"
        # Don't create the file → read raises OSError → adapter returns None.
        assert WindsurfAdapter().parse(p) is None

    def test_session_id_is_deterministic_per_path(self, tmp_path):
        p = tmp_path / "plan.md"
        p.write_text("# A\n" + ("x" * 60), encoding="utf-8")
        a = WindsurfAdapter()
        c1 = a.parse(p)
        c2 = a.parse(p)
        assert c1.session_id == c2.session_id

    def test_discover_picks_up_md_and_txt(self, tmp_path, monkeypatch):
        root = tmp_path / "plans"
        root.mkdir()
        (root / "a.md").write_text("# a\n" + "x" * 60)
        (root / "b.txt").write_text("body " + "y" * 60)
        (root / "ignored.json").write_text("{}")
        a = WindsurfAdapter()
        monkeypatch.setattr(a, "home", root)
        names = sorted(p.name for p in a.discover())
        assert names == ["a.md", "b.txt"]
