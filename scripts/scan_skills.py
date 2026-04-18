#!/usr/bin/env python3
"""
Scannt alle Claude Code Skills und speichert Metadaten in die DB.
Sucht in: ~/.claude/skills/ (global) und allen .claude/skills/ in Git-Repos.
"""

import os
import re
import sys
import glob
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import Json

DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}


def _connect() -> "psycopg2.extensions.connection":
    """Connect to PostgreSQL with a friendly error if the DB is unreachable."""
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}.\n"
            f"  Is it running? Try: docker compose up -d\n"
            f"  Or: brew services start postgresql@16\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e

GLOBAL_SKILLS = Path.home() / ".claude" / "skills"
# Projekt-Skills via glob
PROJECT_PATTERNS = [
    str(Path.home() / "Documents/GitHub/*/.claude/skills"),
    str(Path.home() / "Library/CloudStorage/*/.claude/skills"),
]


def parse_skill_md(skill_md_path: Path) -> dict[str, Any]:
    """Parst SKILL.md — extrahiert Frontmatter + Description."""
    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except Exception:
        return {}

    result = {"name": skill_md_path.parent.name, "description": ""}

    # YAML Frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            fm = content[3:end]
            for line in fm.split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key in ("name", "description", "version"):
                        result[key] = val

    # Trigger-Wörter aus Description extrahieren
    desc = result.get("description", "")
    triggers = []
    # Suche nach "Trigger bei/on:" oder "Triggers:"
    trigger_match = re.search(r'[Tt]rigger[s]?\s+(?:bei|on|:)\s*["\']([^"\']+)["\']', desc)
    if trigger_match:
        triggers = [t.strip() for t in trigger_match.group(1).split(",")]
    # Alternativ: Wörter in Quotes extrahieren
    else:
        triggers = re.findall(r'["\']([a-zA-Z0-9äöüßÄÖÜ][a-zA-Z0-9äöüßÄÖÜ\s\-/]+)["\']', desc)[:10]

    result["triggers"] = triggers
    return result


def scan_directory(skills_dir: Path, skill_type: str) -> list[dict[str, Any]]:
    """Scannt ein Skills-Verzeichnis. Gibt Liste von Skill-Dicts zurück."""
    skills: list[dict[str, Any]] = []
    if not skills_dir.exists():
        return skills

    for skill_path in skills_dir.iterdir():
        if not skill_path.is_dir():
            continue
        skill_md = skill_path / "SKILL.md"
        if not skill_md.exists():
            continue

        meta = parse_skill_md(skill_md)
        stat = skill_md.stat()
        skills.append({
            "name": meta.get("name", skill_path.name),
            "version": meta.get("version", "1.0.0"),
            "description": meta.get("description", ""),
            "path": str(skill_path),
            "triggers": meta.get("triggers", []),
            "skill_type": skill_type,
            "file_created": datetime.fromtimestamp(stat.st_birthtime, tz=timezone.utc) if hasattr(stat, "st_birthtime") else datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
            "file_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        })
    return skills


def main() -> None:
    print("=" * 60)
    print("Claude Memory DB — Skill Scanner")
    print("=" * 60)

    all_skills: list[dict[str, Any]] = []

    # Global
    print(f"\nScanne: {GLOBAL_SKILLS}")
    all_skills.extend(scan_directory(GLOBAL_SKILLS, "global"))

    # Projekt-Skills
    for pattern in PROJECT_PATTERNS:
        for skills_dir in glob.glob(pattern):
            skills_path = Path(skills_dir)
            print(f"Scanne: {skills_path}")
            all_skills.extend(scan_directory(skills_path, "project"))

    print(f"\nGefunden: {len(all_skills)} Skills")

    conn = _connect()
    cursor = conn.cursor()

    inserted = 0
    updated = 0
    for skill in all_skills:
        try:
            cursor.execute("""
                INSERT INTO skills (name, version, description, path, triggers, config, file_created, file_modified)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name, path) DO UPDATE SET
                    version = EXCLUDED.version,
                    description = EXCLUDED.description,
                    triggers = EXCLUDED.triggers,
                    file_created = EXCLUDED.file_created,
                    file_modified = EXCLUDED.file_modified,
                    updated_at = now()
                RETURNING (xmax = 0) AS inserted
            """, (
                skill["name"],
                skill["version"],
                skill["description"][:2000],
                skill["path"],
                skill["triggers"],
                Json({"skill_type": skill["skill_type"]}),
                skill["file_created"],
                skill["file_modified"],
            ))
            row = cursor.fetchone()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            print(f"  ✗ {skill['name']}: {e}")

    conn.commit()
    cursor.close()
    conn.close()

    print(f"\n{'=' * 60}")
    print(f"Neu:         {inserted}")
    print(f"Aktualisiert: {updated}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
