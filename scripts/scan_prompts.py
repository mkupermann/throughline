#!/usr/bin/env python3
"""
Scannt CLAUDE.md-Dateien aus allen Projekten sowie Skill-Prompts
und speichert sie als wiederverwendbare Templates in der prompts-Tabelle.
"""

import os
import re
import sys
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import Json

DB: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}


def _connect() -> "psycopg2.extensions.connection":
    """Connect to PostgreSQL with a friendly error if the DB is unreachable."""
    try:
        return psycopg2.connect(**DB)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{DB['host']}:{DB['port']}/{DB['dbname']}.\n"
            f"  Is it running? Try: docker compose up -d\n"
            f"  Or: brew services start postgresql@16\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e

# Typische Pfade für CLAUDE.md (schnelle Scans, KEIN Google Drive rglob)
HOME = Path.home()
SEARCH_PATHS = [
    HOME,                                                # ~/CLAUDE.md (global user)
    HOME / ".claude",                                   # ~/.claude/CLAUDE.md
    HOME / "Documents/GitHub",                          # GitHub-Repos
]
GLOBAL_SKILLS = HOME / ".claude/skills"
MAX_DEPTH = 4


def find_claude_mds() -> list[Path]:
    """Findet CLAUDE.md via find (schnell, mit prune)."""
    found = set()
    for root in SEARCH_PATHS:
        if not root.exists():
            continue
        # Direkt in root?
        direct = root / "CLAUDE.md"
        if direct.is_file():
            found.add(direct)
        # find mit prune für node_modules etc. + Tiefe-Limit
        try:
            result = subprocess.run(
                [
                    "find", str(root),
                    "-maxdepth", str(MAX_DEPTH + 1),
                    "(",
                    "-name", "node_modules", "-o",
                    "-name", ".git", "-o",
                    "-name", ".venv", "-o",
                    "-name", "venv", "-o",
                    "-name", "__pycache__", "-o",
                    "-name", "dist", "-o",
                    "-name", "build", "-o",
                    "-name", ".next", "-o",
                    "-name", ".cache", "-o",
                    "-name", "CloudStorage",  # Google Drive skip
                    ")",
                    "-prune", "-o",
                    "-type", "f", "-name", "CLAUDE.md", "-print"
                ],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and line.endswith("CLAUDE.md"):
                    found.add(Path(line))
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"  ! find in {root}: {e}")
            continue
    return sorted(found)


def extract_variables(content: str) -> list[str]:
    """Extrahiert Template-Variablen wie {{name}} oder ${name}."""
    found: set[str] = set()
    for m in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", content):
        found.add(m)
    for m in re.findall(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", content):
        found.add(m)
    return sorted(found)


def project_name_from_path(path: Path) -> str:
    """Leitet Projekt-Namen aus Pfad ab."""
    parts = path.parts
    # CLAUDE.md in Home → "global"
    if path.parent == Path.home():
        return "global"
    # Unter ~/.claude → "claude-config"
    if ".claude" in parts:
        return "claude-config"
    # GitHub-Repo: 3 Ebenen nach Documents/GitHub
    if "GitHub" in parts:
        idx = parts.index("GitHub")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    # Google Drive → ab "Meine Ablage"
    if "Meine Ablage" in parts:
        idx = parts.index("Meine Ablage")
        if idx + 1 < len(parts):
            return f"drive/{parts[idx + 1]}"
    # Fallback: parent-dir
    return path.parent.name


def parse_skill_frontmatter(content: str) -> dict[str, Any]:
    """Parst YAML-Frontmatter einer SKILL.md."""
    result: dict[str, Any] = {"name": None, "description": None}
    if not content.startswith("---"):
        return result
    end = content.find("---", 3)
    if end < 0:
        return result
    for line in content[3:end].split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in ("name", "description"):
                result[k] = v
    return result


def ingest_claude_md(cur: Any, filepath: Path, stats: dict[str, int]) -> bool:
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    if len(content) < 20:
        return False

    name = f"CLAUDE.md@{project_name_from_path(filepath)}"
    # Dedupe per project — keep newest
    tags = ["claude_md", project_name_from_path(filepath)]
    variables = extract_variables(content)
    category = "claude_md"

    stat = filepath.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    try:
        cur.execute("""
            INSERT INTO prompts (name, category, content, variables, source_path, tags, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                content = EXCLUDED.content,
                variables = EXCLUDED.variables,
                source_path = EXCLUDED.source_path,
                tags = EXCLUDED.tags,
                updated_at = now()
            RETURNING (xmax = 0) AS inserted
        """, (
            name[:200], category, content[:10000], Json(variables),
            str(filepath), tags, mtime
        ))
        row = cur.fetchone()
        if row and row[0]:
            stats["new"] += 1
        else:
            stats["updated"] += 1
        return True
    except Exception as e:
        print(f"  ! {filepath}: {e}")
        stats["errors"] += 1
        return False


def ingest_skill_prompts(cur: Any, stats: dict[str, int]) -> None:
    """Speichert jede SKILL.md als Prompt-Template mit category=skill."""
    if not GLOBAL_SKILLS.exists():
        return
    for skill_dir in GLOBAL_SKILLS.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(content) < 20:
            continue
        meta = parse_skill_frontmatter(content)
        skill_name = meta["name"] or skill_dir.name
        name = f"skill/{skill_name}"
        variables = extract_variables(content)
        stat = skill_md.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        try:
            cur.execute("""
                INSERT INTO prompts (name, category, content, variables, source_path, tags, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    content = EXCLUDED.content,
                    variables = EXCLUDED.variables,
                    source_path = EXCLUDED.source_path,
                    tags = EXCLUDED.tags,
                    updated_at = now()
                RETURNING (xmax = 0) AS inserted
            """, (
                name[:200], "skill", content[:10000], Json(variables),
                str(skill_md), ["skill", skill_name], mtime
            ))
            row = cur.fetchone()
            if row and row[0]:
                stats["new"] += 1
            else:
                stats["updated"] += 1
        except Exception as e:
            print(f"  ! {skill_md}: {e}")
            stats["errors"] += 1


def main() -> None:
    print("=" * 60)
    print("Prompt-Scanner — CLAUDE.md + Skill-Templates")
    print("=" * 60)

    claude_mds = find_claude_mds()
    print(f"\nGefunden: {len(claude_mds)} CLAUDE.md Dateien")

    conn = _connect()
    cur = conn.cursor()

    stats: dict[str, int] = {"new": 0, "updated": 0, "errors": 0}

    # CLAUDE.md ingesten
    for fp in claude_mds:
        if ingest_claude_md(cur, fp, stats):
            conn.commit()
            print(f"  ✓ {fp}")

    # Skills als Templates speichern
    print("\nSkills als Prompt-Templates:")
    ingest_skill_prompts(cur, stats)
    conn.commit()

    print(f"\n{'=' * 60}")
    print(f"Neu:         {stats['new']}")
    print(f"Aktualisiert: {stats['updated']}")
    print(f"Fehler:      {stats['errors']}")
    print(f"{'=' * 60}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
