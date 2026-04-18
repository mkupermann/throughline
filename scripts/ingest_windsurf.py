#!/usr/bin/env python3
"""
Windsurf-Ingestion: Liest Windsurf-Pläne (~/.windsurf/plans/*.md) als Conversations
und schreibt sie in die claude_memory DB.
"""

import os
import hashlib
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import Json

DB = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}
PLANS_DIR = Path.home() / ".windsurf" / "plans"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_title(content: str, filename: str) -> str:
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if m:
        return m.group(1).strip()[:200]
    # Fallback: Dateiname ohne Hash-Suffix
    stem = Path(filename).stem
    stem = re.sub(r"-[a-f0-9]{6}$", "", stem)
    return stem.replace("-", " ").replace("_", " ").title()[:200]


def ingest_plan(cursor, filepath: Path) -> bool:
    content = filepath.read_text(encoding="utf-8", errors="ignore")
    if len(content) < 50:
        return False

    file_hash = sha256_file(filepath)

    # Schon ingestiert?
    cursor.execute(
        "SELECT 1 FROM ingestion_log WHERE file_path = %s AND file_hash = %s",
        (str(filepath), file_hash)
    )
    if cursor.fetchone():
        return False

    title = extract_title(content, filepath.name)
    mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
    ctime = datetime.fromtimestamp(filepath.stat().st_ctime, tz=timezone.utc)
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(filepath)))

    # Conversation einfügen
    cursor.execute("""
        INSERT INTO conversations
          (session_id, project_path, model, entrypoint, started_at, ended_at,
           message_count, summary, tags, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id) DO NOTHING
        RETURNING id
    """, (
        session_id,
        "windsurf:plans",
        "windsurf-cascade",
        "windsurf",
        ctime, mtime,
        1, title,
        ["windsurf", "plan"],
        Json({"source": "windsurf", "file": filepath.name, "kind": "plan"})
    ))
    row = cursor.fetchone()
    if not row:
        return False
    conv_id = row[0]

    # Plan-Inhalt als user-Message speichern
    cursor.execute("""
        INSERT INTO messages
          (conversation_id, role, content, created_at, metadata)
        VALUES (%s, %s, %s, %s, %s)
    """, (conv_id, "user", content, ctime, Json({"source_file": str(filepath)})))

    cursor.execute("""
        INSERT INTO ingestion_log (file_path, file_hash, record_count)
        VALUES (%s, %s, 1)
        ON CONFLICT DO NOTHING
    """, (str(filepath), file_hash))

    return True


def main():
    print("=" * 60)
    print("Windsurf Ingestion — Plans")
    print("=" * 60)

    if not PLANS_DIR.exists():
        print(f"Nichts zu tun — {PLANS_DIR} existiert nicht.")
        return

    files = list(PLANS_DIR.glob("*.md")) + list(PLANS_DIR.glob("*.txt"))
    print(f"\n{len(files)} Dateien gefunden\n")

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    ingested = 0
    skipped = 0
    errors = 0

    for fp in files:
        try:
            if ingest_plan(cur, fp):
                conn.commit()
                ingested += 1
                print(f"  + {fp.name}")
            else:
                skipped += 1
        except Exception as e:
            conn.rollback()
            errors += 1
            print(f"  ! {fp.name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"Ingestiert:   {ingested}")
    print(f"Übersprungen: {skipped}")
    print(f"Fehler:       {errors}")
    print(f"{'=' * 60}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
