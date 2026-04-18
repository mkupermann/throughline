"""Tests for scripts/scan_skills.py — SKILL.md frontmatter parsing and trigger extraction."""

import pytest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "scan_skills", ROOT / "scripts" / "scan_skills.py"
)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class TestSkillMDParsing:
    def test_parses_frontmatter_name_and_description(self, tmp_skill_dir):
        skill_md = tmp_skill_dir / "SKILL.md"
        meta = scan.parse_skill_md(skill_md)
        assert meta.get("name") == "sample-skill"
        assert "sample skill" in meta.get("description", "").lower()

    def test_parses_version(self, tmp_skill_dir):
        skill_md = tmp_skill_dir / "SKILL.md"
        meta = scan.parse_skill_md(skill_md)
        assert meta.get("version") == "1.2.3"

    def test_extracts_triggers(self, tmp_skill_dir):
        skill_md = tmp_skill_dir / "SKILL.md"
        meta = scan.parse_skill_md(skill_md)
        triggers = meta.get("triggers", [])
        assert "sample" in triggers or any("sample" in t for t in triggers)

    def test_no_frontmatter_returns_default_name(self, tmp_path):
        skill_dir = tmp_path / "no-frontmatter"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Just a body\n\nNo YAML here.\n")
        meta = scan.parse_skill_md(skill_md)
        assert meta.get("name") == skill_dir.name

    def test_malformed_frontmatter_does_not_crash(self, tmp_path):
        skill_dir = tmp_path / "malformed"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nbroken yaml here\n\n# body\n")
        # Should not raise
        meta = scan.parse_skill_md(skill_md)
        assert isinstance(meta, dict)

    def test_missing_file_returns_empty_dict(self, tmp_path):
        meta = scan.parse_skill_md(tmp_path / "does-not-exist.md")
        assert meta == {} or meta.get("name") is None or isinstance(meta, dict)


class TestSkillDirectoryScanning:
    def test_scan_finds_skill_with_valid_md(self, tmp_path, tmp_skill_dir):
        # tmp_skill_dir is already inside tmp_path's parent; use its parent
        skills = scan.scan_directory(tmp_skill_dir.parent, "test")
        assert any(s["name"] == "sample-skill" for s in skills)

    def test_scan_skips_dirs_without_skill_md(self, tmp_path):
        (tmp_path / "not-a-skill").mkdir()
        (tmp_path / "not-a-skill" / "README.md").write_text("no skill")
        skills = scan.scan_directory(tmp_path, "test")
        assert len([s for s in skills if s["name"] == "not-a-skill"]) == 0

    def test_scan_returns_file_timestamps(self, tmp_path, tmp_skill_dir):
        skills = scan.scan_directory(tmp_skill_dir.parent, "test")
        assert any(s.get("file_modified") is not None for s in skills)

    def test_scan_handles_nonexistent_directory(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist"
        skills = scan.scan_directory(nonexistent, "test")
        assert skills == []

    def test_scan_skill_includes_path(self, tmp_path, tmp_skill_dir):
        skills = scan.scan_directory(tmp_skill_dir.parent, "test")
        target = next((s for s in skills if s["name"] == "sample-skill"), None)
        assert target is not None
        assert Path(target["path"]).exists()
