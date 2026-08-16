#!/usr/bin/env python3
"""
Session ingestion for the Throughline database.
Liest Claude Code JSONL-Sessions und speichert sie in PostgreSQL.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json

DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "throughline"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


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


def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_content(message: dict[str, Any]) -> str:
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


def extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrahiert Tool-Calls aus content blocks."""
    content = message.get("content", [])
    if not isinstance(content, list):
        return []
    calls: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            calls.append(
                {
                    "tool_name": block.get("name", ""),
                    "input": block.get("input", {}),
                }
            )
    return calls


def map_role(entry: dict[str, Any]) -> str:
    """Mappt JSONL type/role auf DB message_role enum."""
    entry_type = entry.get("type", "")
    msg = entry.get("message", {})
    role = msg.get("role", "")

    if entry_type == "user" or role == "user":
        # Is this a tool_result?
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


def _first_cwd(entries: list[dict[str, Any]]) -> str | None:
    """Return the first non-empty ``cwd`` field across all entries.

    Claude Code records the user's working directory on most entries.
    Reading it directly is correct; the historical alternative —
    reconstructing a path from the on-disk session-hash directory name
    by replacing every ``-`` with ``/`` — silently mangles any project
    whose name contains a hyphen (``claude-memory-db`` → ``claude/memory/db``).
    """
    for e in entries:
        cwd = e.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return cwd
    return None


def ingest_file(cursor: Any, filepath: Path, project_path: str | None) -> int:
    """Ingest one JSONL file. Returns how many messages were inserted.

    The ``project_path`` arg is now a *fallback*: if any JSONL entry
    carries a real ``cwd`` we prefer that. The fallback is still useful
    for older JSONL files that pre-date the cwd field.
    """
    entries: list[dict[str, Any]] = []
    with open(filepath, encoding="utf-8") as f:
        for _line_num, line in enumerate(f, 1):
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

    # Keep only message entries (they carry a 'message' key)
    msg_entries = [e for e in entries if "message" in e and isinstance(e.get("message"), dict)]
    if not msg_entries:
        return 0

    # Prefer the JSONL-recorded cwd over the hash-derived fallback.
    real_cwd = _first_cwd(entries)
    if real_cwd:
        project_path = real_cwd

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

    # Pre-aggregate token usage so the conversations row carries totals on
    # the way in. The Anthropic usage shape on assistant messages is
    #   {input_tokens, output_tokens, cache_creation_input_tokens,
    #    cache_read_input_tokens, …}
    # The historically stored "token_count_in" maps to the sum of all input
    # categories (raw + cache-creation + cache-read), since each is a real
    # cost-incurring input read; "token_count_out" is just output_tokens.
    conv_tokens_in, conv_tokens_out = _sum_usage(msg_entries)

    # Insert the conversation
    try:
        cursor.execute(
            """
            INSERT INTO conversations (session_id, project_path, model, entrypoint, git_branch,
                                       started_at, ended_at, message_count,
                                       token_count_in, token_count_out, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO NOTHING
            RETURNING id
        """,
            (
                session_id,
                project_path,
                model,
                entrypoint,
                git_branch,
                started_at,
                ended_at,
                len(msg_entries),
                conv_tokens_in,
                conv_tokens_out,
                Json({}),
            ),
        )
        result = cursor.fetchone()
        if result is None:
            return 0  # Already exists
        conv_id = result[0]
    except Exception as e:
        print(f"  Fehler bei Conversation {session_id}: {e}")
        return 0

    # Insert the messages
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
        # Per-message token total: assistant messages carry usage; user
        # messages don't. Storing total (in+out) so a single column reads
        # as "tokens this turn cost".
        msg_token_count = _per_message_total(msg)

        try:
            cursor.execute(
                """
                INSERT INTO messages (conversation_id, uuid, parent_uuid, role, content,
                                     content_blocks, tool_calls, tool_name, is_sidechain,
                                     model, token_count, created_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    conv_id,
                    uuid_val,
                    parent_uuid,
                    role,
                    content,
                    Json(msg.get("content")) if isinstance(msg.get("content"), list) else None,
                    Json(tool_calls) if tool_calls else None,
                    tool_name,
                    is_sidechain,
                    msg_model,
                    msg_token_count,
                    ts,
                    Json({}),
                ),
            )
            msg_count += 1
        except Exception as e:
            print(f"  Fehler bei Message: {e}")
            continue

    return msg_count


def _per_message_total(message: dict[str, Any]) -> int | None:
    """Per-message tokens total. None when no usage is present."""
    u = message.get("usage")
    if not isinstance(u, dict):
        return None
    return (
        int(u.get("input_tokens") or 0)
        + int(u.get("cache_creation_input_tokens") or 0)
        + int(u.get("cache_read_input_tokens") or 0)
        + int(u.get("output_tokens") or 0)
    ) or None


def _sum_usage(entries: list[dict[str, Any]]) -> tuple[int, int]:
    """Sum input vs output tokens across all assistant messages in entries."""
    in_total = 0
    out_total = 0
    for e in entries:
        m = e.get("message")
        if not isinstance(m, dict):
            continue
        u = m.get("usage")
        if not isinstance(u, dict):
            continue
        in_total += (
            int(u.get("input_tokens") or 0)
            + int(u.get("cache_creation_input_tokens") or 0)
            + int(u.get("cache_read_input_tokens") or 0)
        )
        out_total += int(u.get("output_tokens") or 0)
    return in_total, out_total


def main() -> None:
    print("=" * 60)
    print("Claude Memory DB — Session Ingestion")
    print("=" * 60)

    conn = _connect()
    cursor = conn.cursor()

    # Alle JSONL-Dateien finden
    jsonl_files: list[tuple[Path, str]] = []
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
            "SELECT 1 FROM ingestion_log WHERE file_path = %s AND file_hash = %s", (str(filepath), file_hash)
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
                cursor.execute(
                    """
                    INSERT INTO ingestion_log (file_path, file_hash, record_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                """,
                    (str(filepath), file_hash, msg_count),
                )
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
    print("Ergebnis:")
    print(f"  Ingestiert:  {ingested} Sessions ({total_messages} Messages)")
    print(f"  Skipped: {skipped}")
    print(f"  Fehler:      {errors}")
    print(f"{'=' * 60}")

    # Auto-materialise the projects table from observed project_names so the
    # GUI's Projects page is not empty on first run. Idempotent (ON CONFLICT
    # DO NOTHING in insert_missing), never touches existing rows. Only runs
    # when this invocation actually wrote something — re-runs that ingest 0
    # new sessions don't need to re-scan.
    if ingested > 0:
        try:
            from .backfill_projects import (
                collect_observed_names,
                existing_project_names,
                insert_missing,
            )

            observed = collect_observed_names(conn, include_conversations=True)
            existing = existing_project_names(conn)
            to_insert = [n for n in observed if n not in existing]
            if to_insert:
                insert_missing(conn, to_insert)
                print(
                    f"  Projects:    materialised {len(to_insert)} new row(s) ({len(existing) + len(to_insert)} total)."
                )
        except Exception as exc:
            # Backfill is a nice-to-have; ingestion itself succeeded.
            print(f"  Projects:    backfill skipped ({exc.__class__.__name__})")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
