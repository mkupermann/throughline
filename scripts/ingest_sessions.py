#!/usr/bin/env python3
"""
Session-Ingestion für claude_memory DB.
Liest Claude Code JSONL-Sessions und speichert sie in PostgreSQL.
"""

import json
import hashlib
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import Json

DB_CONFIG = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_content(message: dict) -> str:
    """Extrahiert lesbaren Text aus message.content (String oder List of Blocks)."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    tc = block.get("content", "")
                    if isinstance(tc, str):
                        parts.append(tc[:500])
                elif block.get("type") == "thinking":
                    pass  # Skip thinking blocks
        return "\n".join(parts)
    return str(content)[:2000]


def extract_tool_calls(message: dict) -> list:
    """Extrahiert Tool-Calls aus content blocks."""
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    calls = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            calls.append({
                "tool_name": block.get("name", ""),
                "input": block.get("input", {}),
            })
    return calls


def map_role(entry: dict) -> str:
    """Mappt JSONL type/role auf DB message_role enum."""
    entry_type = entry.get("type", "")
    msg = entry.get("message", {})
    role = msg.get("role", "")

    if entry_type == "user" or role == "user":
        # Prüfe ob es ein tool_result ist
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return "tool_result"
        return "user"
    elif entry_type == "assistant" or role == "assistant":
        return "assistant"
    elif entry_type == "system" or role == "system":
        return "system"
    return "user"


def parse_timestamp(ts_str: str) -> datetime:
    """Parst ISO-Timestamp."""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def ingest_file(cursor, filepath: Path, project_path: str):
    """Ingestiert eine einzelne JSONL-Datei."""
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    if not entries:
        return 0

    # Filtere auf Message-Einträge (haben 'message' key)
    msg_entries = [e for e in entries if "message" in e and isinstance(e.get("message"), dict)]
    if not msg_entries:
        return 0

    # Session-Metadaten aus erstem Eintrag
    first = msg_entries[0]
    session_id = first.get("sessionId")
    if not session_id:
        return 0

    model = None
    entrypoint = first.get("entrypoint", "")
    git_branch = first.get("gitBranch", "")
    started_at = parse_timestamp(first.get("timestamp"))
    ended_at = parse_timestamp(msg_entries[-1].get("timestamp"))

    # Model aus assistant-Messages extrahieren
    for e in msg_entries:
        m = e.get("message", {})
        if m.get("role") == "assistant" and m.get("model"):
            model = m["model"]
            break

    # Conversation einfügen
    try:
        cursor.execute("""
            INSERT INTO conversations (session_id, project_path, model, entrypoint, git_branch,
                                       started_at, ended_at, message_count, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
            RETURNING id
        """, (
            session_id, project_path, model, entrypoint, git_branch,
            started_at, ended_at, len(msg_entries), Json({})
        ))
        result = cursor.fetchone()
        if result is None:
            return 0  # Already exists
        conv_id = result[0]
    except Exception as e:
        print(f"  Fehler bei Conversation {session_id}: {e}")
        return 0

    # Messages einfügen
    msg_count = 0
    for entry in msg_entries:
        msg = entry.get("message", {})
        role = map_role(entry)
        content = extract_content(msg)
        tool_calls = extract_tool_calls(msg)
        tool_name = None
        if tool_calls:
            tool_name = tool_calls[0].get("tool_name")

        ts = parse_timestamp(entry.get("timestamp"))
        uuid_val = entry.get("uuid")
        parent_uuid = entry.get("parentUuid")
        is_sidechain = entry.get("isSidechain", False)
        msg_model = msg.get("model")

        try:
            cursor.execute("""
                INSERT INTO messages (conversation_id, uuid, parent_uuid, role, content,
                                     content_blocks, tool_calls, tool_name, is_sidechain,
                                     model, created_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                conv_id, uuid_val, parent_uuid, role, content,
                Json(msg.get("content")) if isinstance(msg.get("content"), list) else None,
                Json(tool_calls) if tool_calls else None,
                tool_name, is_sidechain, msg_model, ts, Json({})
            ))
            msg_count += 1
        except Exception as e:
            print(f"  Fehler bei Message: {e}")
            continue

    return msg_count


def main():
    print("=" * 60)
    print("Claude Memory DB — Session Ingestion")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Alle JSONL-Dateien finden
    jsonl_files = []
    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if project_dir.is_dir():
                for jsonl in project_dir.glob("*.jsonl"):
                    jsonl_files.append((jsonl, project_dir.name))

    print(f"\nGefunden: {len(jsonl_files)} JSONL-Dateien")

    ingested = 0
    skipped = 0
    total_messages = 0
    errors = 0

    for filepath, project_hash in jsonl_files:
        file_hash = sha256_file(filepath)

        # Bereits ingestiert?
        cursor.execute(
            "SELECT 1 FROM ingestion_log WHERE file_path = %s AND file_hash = %s",
            (str(filepath), file_hash)
        )
        if cursor.fetchone():
            skipped += 1
            continue

        # Projekt-Pfad ableiten
        project_path = project_hash.replace("-", "/") if project_hash != "-" else None

        try:
            msg_count = ingest_file(cursor, filepath, project_path)
            if msg_count > 0:
                # In ingestion_log eintragen
                cursor.execute("""
                    INSERT INTO ingestion_log (file_path, file_hash, record_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (str(filepath), file_hash, msg_count))
                conn.commit()
                ingested += 1
                total_messages += msg_count
                print(f"  ✓ {filepath.name}: {msg_count} Messages")
            else:
                skipped += 1
                conn.rollback()
        except Exception as e:
            conn.rollback()
            errors += 1
            print(f"  ✗ {filepath.name}: {e}")

    print(f"\n{'=' * 60}")
    print(f"Ergebnis:")
    print(f"  Ingestiert:  {ingested} Sessions ({total_messages} Messages)")
    print(f"  Übersprungen: {skipped}")
    print(f"  Fehler:      {errors}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
