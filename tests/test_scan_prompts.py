"""Tests for scripts/scan_prompts.py — CLAUDE.md discovery, variable extraction, project name derivation."""

from pathlib import Path

from throughline.jobs import scan_prompts as sp


def test_skill_ingestion_rolls_back_the_cursor_connection_on_failure(tmp_path, monkeypatch):
    """One bad skill must not leave its failed transaction open."""

    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: broken-skill\n---\n\nBody long enough to ingest.\n",
        encoding="utf-8",
    )

    class Connection:
        def __init__(self):
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    class Cursor:
        def __init__(self):
            self.connection = Connection()

        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database rejected row")

    cursor = Cursor()
    stats = {"new": 0, "updated": 0, "errors": 0}
    monkeypatch.setattr(sp, "GLOBAL_SKILLS", tmp_path)

    sp.ingest_skill_prompts(cursor, stats)

    assert cursor.connection.rollbacks == 1
    assert stats["errors"] == 1


class TestVariableExtraction:
    def test_extracts_mustache_style(self):
        content = "Hello {{name}}, welcome to {{project}}"
        vars_ = sp.extract_variables(content)
        assert "name" in vars_
        assert "project" in vars_

    def test_extracts_shell_style(self):
        content = "Path: ${HOME}/.config/${APP}"
        vars_ = sp.extract_variables(content)
        assert "HOME" in vars_
        assert "APP" in vars_

    def test_extracts_mixed_styles(self):
        content = "{{name}} lives at ${path}"
        vars_ = sp.extract_variables(content)
        assert "name" in vars_
        assert "path" in vars_

    def test_deduplicates_variables(self):
        content = "{{x}} {{x}} {{x}}"
        vars_ = sp.extract_variables(content)
        assert vars_.count("x") == 1

    def test_ignores_whitespace_in_braces(self):
        content = "{{  spaced  }}"
        vars_ = sp.extract_variables(content)
        assert "spaced" in vars_

    def test_no_variables_returns_empty_list(self):
        content = "Just plain text"
        assert sp.extract_variables(content) == []

    def test_numeric_variables_not_extracted(self):
        # Variables must start with letter/underscore
        content = "{{123}}"
        vars_ = sp.extract_variables(content)
        assert "123" not in vars_


class TestProjectNameDerivation:
    def test_home_directory_returns_global(self):
        path = Path.home() / "CLAUDE.md"
        name = sp.project_name_from_path(path)
        assert name == "global"

    def test_claude_config_directory(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        path = claude_dir / "CLAUDE.md"
        path.touch()
        name = sp.project_name_from_path(path)
        assert name == "claude-config"

    def test_github_repo_path(self):
        # Build a path that has "GitHub" somewhere
        repo_path = Path("workspace") / "GitHub" / "my-awesome-repo" / "CLAUDE.md"
        name = sp.project_name_from_path(repo_path)
        assert name == "my-awesome-repo"

    def test_fallback_uses_parent_dir(self):
        path = Path("workspace") / "some-project" / "CLAUDE.md"
        name = sp.project_name_from_path(path)
        assert name == "some-project"


class TestSkillFrontmatterParsing:
    def test_parses_name_and_description(self):
        content = "---\nname: test-skill\ndescription: does testing\n---\nbody\n"
        meta = sp.parse_skill_frontmatter(content)
        assert meta.get("name") == "test-skill"
        assert meta.get("description") == "does testing"

    def test_no_frontmatter_returns_none_values(self):
        content = "# Just markdown\n\nNo frontmatter\n"
        meta = sp.parse_skill_frontmatter(content)
        assert meta.get("name") is None
        assert meta.get("description") is None

    def test_handles_quoted_values(self):
        content = "---\nname: \"quoted-name\"\ndescription: 'single-quoted'\n---\n"
        meta = sp.parse_skill_frontmatter(content)
        assert meta.get("name") == "quoted-name"
        assert meta.get("description") == "single-quoted"
