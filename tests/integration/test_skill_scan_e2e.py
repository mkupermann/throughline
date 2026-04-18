"""End-to-end test: scan a skill directory and verify rows in the skills table."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _make_skill(root: Path, name: str, description: str, version: str = "1.0.0") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"version: {version}\n"
        "---\n\n"
        f"# {name}\n\nBody of the skill.\n",
        encoding="utf-8",
    )
    return skill_dir


def test_scan_skills_inserts_rows(tmp_path, db_env, db_connection, monkeypatch):
    skills_root = tmp_path / "home" / ".claude" / "skills"
    _make_skill(
        skills_root,
        "alpha-skill",
        'Performs alpha duties. Trigger bei "alpha", "test-alpha"',
        version="1.2.3",
    )
    _make_skill(
        skills_root,
        "beta-skill",
        'Handles beta tasks. Trigger bei "beta"',
    )

    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    import importlib
    import sys as _sys

    _sys.modules.pop("scan_skills", None)
    scan_skills = importlib.import_module("scan_skills")
    scan_skills.GLOBAL_SKILLS = skills_root
    scan_skills.PROJECT_PATTERNS = []  # avoid scanning the real user home
    scan_skills.DB_CONFIG = {
        "dbname": db_env["dbname"],
        "user": db_env["user"],
        "host": db_env["host"],
        "port": db_env["port"],
    }

    scan_skills.main()

    with db_connection.cursor() as cur:
        cur.execute("SELECT name, version FROM skills ORDER BY name")
        rows = cur.fetchall()
        assert [r[0] for r in rows] == ["alpha-skill", "beta-skill"]
        assert rows[0][1] == "1.2.3"

        cur.execute("SELECT triggers FROM skills WHERE name = 'alpha-skill'")
        triggers = cur.fetchone()[0]
        assert triggers is not None
        assert any("alpha" in t for t in triggers)


def test_scan_skills_upserts_on_rerun(tmp_path, db_env, db_connection, monkeypatch):
    """A second scan with an updated description should update the row, not duplicate it."""
    skills_root = tmp_path / "home" / ".claude" / "skills"
    skill_dir = _make_skill(skills_root, "evolving-skill", 'v1. Trigger bei "old"')

    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    import importlib
    import sys as _sys

    _sys.modules.pop("scan_skills", None)
    scan_skills = importlib.import_module("scan_skills")
    scan_skills.GLOBAL_SKILLS = skills_root
    scan_skills.PROJECT_PATTERNS = []
    scan_skills.DB_CONFIG = {
        "dbname": db_env["dbname"],
        "user": db_env["user"],
        "host": db_env["host"],
        "port": db_env["port"],
    }

    scan_skills.main()

    # Update the skill file and re-scan.
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: evolving-skill\n"
        'description: v2. Trigger bei "new"\n'
        "version: 2.0.0\n"
        "---\n\n# updated\n",
        encoding="utf-8",
    )
    scan_skills.main()

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM skills")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT version, description FROM skills WHERE name = 'evolving-skill'")
        version, description = cur.fetchone()
        assert version == "2.0.0"
        assert "v2" in description
