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


# --------------------------------------------------------------------------- #
# The identifier has to survive a different machine                           #
# --------------------------------------------------------------------------- #


def test_the_same_plan_gets_the_same_id_wherever_it_is_mounted(tmp_path):
    """session_id was derived from the absolute path.

    Read the same file from a container (/home/throughline/.windsurf) and from
    a host (/Users/someone/.windsurf) and you got two different sessions for
    one plan. Measured on this corpus: 34 windsurf plans stored twice across
    the native and containerised databases, one set per mount point. It also
    makes cross-machine convergence impossible, because the identifier that is
    supposed to be the same everywhere is the one thing that differs.
    """
    from throughline.adapters.windsurf import WindsurfAdapter

    body = "# Plan\n\n" + "Ein Plan mit genug Inhalt, damit der Adapter ihn annimmt.\n" * 3

    a = tmp_path / "home" / "throughline" / ".windsurf" / "plans"
    b = tmp_path / "Users" / "someone" / ".windsurf" / "plans"
    for folder in (a, b):
        folder.mkdir(parents=True)
        (folder / "refactor-auth.md").write_text(body, encoding="utf-8")

    adapter = WindsurfAdapter()
    first = adapter.parse(a / "refactor-auth.md")
    second = adapter.parse(b / "refactor-auth.md")

    assert first is not None and second is not None
    assert first.session_id == second.session_id


def test_two_different_plans_still_get_different_ids(tmp_path):
    from throughline.adapters.windsurf import WindsurfAdapter

    body = "# Plan\n\n" + "Genug Inhalt, damit der Adapter die Datei annimmt.\n" * 3
    folder = tmp_path / "plans"
    folder.mkdir()
    (folder / "one.md").write_text(body, encoding="utf-8")
    (folder / "two.md").write_text(body, encoding="utf-8")

    adapter = WindsurfAdapter()
    assert adapter.parse(folder / "one.md").session_id != adapter.parse(folder / "two.md").session_id
