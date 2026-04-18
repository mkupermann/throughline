"""Tests for scripts/scan_prompts.py — CLAUDE.md discovery, variable extraction, project name derivation."""

import pytest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "scan_prompts", ROOT / "scripts" / "scan_prompts.py"
)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


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

    def test_github_repo_path(self, tmp_path):
        # Build a path that has "GitHub" somewhere
        repo_path = tmp_path / "GitHub" / "my-awesome-repo" / "CLAUDE.md"
        repo_path.parent.mkdir(parents=True)
        repo_path.touch()
        name = sp.project_name_from_path(repo_path)
        assert name == "my-awesome-repo"

    def test_fallback_uses_parent_dir(self, tmp_path):
        path = tmp_path / "some-project" / "CLAUDE.md"
        path.parent.mkdir()
        path.touch()
        name = sp.project_name_from_path(path)
        assert name == "some-project"


class TestSkillFrontmatterParsing:
    def test_parses_name_and_description(self):
        content = (
            "---\n"
            'name: test-skill\n'
            'description: does testing\n'
            "---\n"
            "body\n"
        )
        meta = sp.parse_skill_frontmatter(content)
        assert meta.get("name") == "test-skill"
        assert meta.get("description") == "does testing"

    def test_no_frontmatter_returns_none_values(self):
        content = "# Just markdown\n\nNo frontmatter\n"
        meta = sp.parse_skill_frontmatter(content)
        assert meta.get("name") is None
        assert meta.get("description") is None

    def test_handles_quoted_values(self):
        content = (
            "---\n"
            'name: "quoted-name"\n'
            "description: 'single-quoted'\n"
            "---\n"
        )
        meta = sp.parse_skill_frontmatter(content)
        assert meta.get("name") == "quoted-name"
        assert meta.get("description") == "single-quoted"
