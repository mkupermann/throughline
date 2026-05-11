#!/usr/bin/env python3
"""Ingest Hermes Agent sessions into the claude_memory DB.

Hermes (https://github.com/.../hermes-agent) stores one JSON file per session
under ``~/.hermes/sessions/session_<ts>_<short>.json``. Schema:

    {
      "session_id":   "20260511_160653_6f89a7",   -- not a UUID
      "model":        "claude-opus-4-7",
      "base_url":     "https://api.anthropic.com",
      "platform":     "cli",
      "session_start":"2026-05-11T16:07:09.607187",
      "last_updated": "2026-05-11T16:09:23.995911",
      "system_prompt":"…",
      "tools":        [{"type": "function", "function": {…}}, …],
      "message_count": 6,
      "messages": [
        {"role": "user",      "content": "…"},
        {"role": "assistant", "content": "…", "reasoning_content": "…", …},
        …
      ]
    }

Mapping onto Throughline's schema:

- ``conversations.session_id`` is a UUID, but Hermes's id is a string.
  We synthesise a deterministic uuid5 from ``"hermes:" + session_id`` so
  re-ingesting the same session reaches the same row.
- ``conversations.project_name`` is fixed to ``hermes`` so Hermes sessions
  bucket together on the GUI's Projects page (and the auto-backfill in
  ``ingest_sessions.py`` will materialise that bucket).
- ``messages.role`` is mapped onto Throughline's
  ``{user, assistant, system, tool_result}`` enum. Hermes "tool" messages
  become ``tool_result``.
- ``messages.metadata`` carries any reasoning_* / finish_reason fields so
  the raw signal isn't lost.

Idempotency is via the same ``ingestion_log`` table the JSONL ingestor uses:
``(file_path, file_hash)``. If the session file has not changed since the
last run, it is skipped. If its hash *has* changed (Hermes is still
appending messages), the conversation's existing messages are deleted and
re-inserted so we don't end up with duplicates.

Usage::

    throughline ingest --hermes
    # or, directly:
    .venv/bin/python scripts/ingest_hermes.py
"""
from _bootstrap import use_venv  # noqa: E402
use_venv()


import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json

DB: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}
HERMES_DIR = Path.home() / ".hermes" / "sessions"

# Stable namespace so uuid5() is reproducible across machines. Anything
# would do; using a fixed v4 keeps the namespace separate from other
# adapters that may also use uuid5.
HERMES_UUID_NAMESPACE = uuid.UUID("c8d4f3b9-6e2a-4b7f-9a1e-3d8e2f6c5b4a")

# Role mapping: Hermes uses OpenAI-style roles; Throughline's enum is
# {user, assistant, system, tool_result}.
_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool_result",
    "function": "tool_result",
}

# Metadata keys we lift off a message and store under messages.metadata.
_MSG_META_KEYS = (
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "finish_reason",
    "tool_call_id",
    "name",
)


def _connect() -> "psycopg2.extensions.connection":
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


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_timestamp(s: str | None) -> datetime:
    """Parse a Hermes ISO timestamp; fall back to now() on anything weird."""
    if not s:
        return datetime.now(timezone.utc)
    try:
        # Hermes writes naive ISO strings like '2026-05-11T16:07:09.607187'
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _session_uuid(session_id: str) -> str:
    return str(uuid.uuid5(HERMES_UUID_NAMESPACE, f"hermes:{session_id}"))


def _message_uuid(session_id: str, index: int) -> str:
    return str(uuid.uuid5(HERMES_UUID_NAMESPACE, f"hermes:{session_id}:msg:{index}"))


def _normalise_content(content: Any) -> tuple[str, list | None]:
    """Return (plain_text, content_blocks_json_or_none).

    Hermes typically uses plain string content but some assistant responses
    can be lists of blocks (when tool calls are involved). Mirror the
    ``messages.content`` / ``messages.content_blocks`` split that the
    Claude Code ingestor uses.
    """
    if isinstance(content, str):
        return content, None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    tc = block.get("content", "")
                    if isinstance(tc, str):
                        parts.append(tc[:500])
        return "\n".join(parts), content
    return str(content)[:2000] if content is not None else "", None


def _message_metadata(msg: dict[str, Any]) -> dict[str, Any]:
    md = {k: msg[k] for k in _MSG_META_KEYS if k in msg and msg[k] is not None}
    return md


def ingest_session_file(cur: Any, filepath: Path) -> int:
    """Ingest one Hermes session file. Returns number of messages written.

    On re-ingest of an unchanged file, ``main()`` short-circuits via
    ingestion_log before calling this. If the hash changed (Hermes
    appended new messages), we delete existing messages for the
    conversation and re-insert fresh so we don't duplicate.
    """
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ✗ {filepath.name}: parse error: {e}")
        return 0

    raw_session_id = data.get("session_id") or filepath.stem
    session_uuid = _session_uuid(str(raw_session_id))
    messages = data.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return 0

    started = parse_timestamp(data.get("session_start"))
    ended = parse_timestamp(data.get("last_updated"))
    model = data.get("model")
    platform = data.get("platform") or "hermes"

    # Conversation metadata that doesn't fit dedicated columns.
    conv_metadata: dict[str, Any] = {
        "source": "hermes",
        "hermes_session_id": raw_session_id,
        "base_url": data.get("base_url"),
        "system_prompt_chars": len(data.get("system_prompt") or ""),
        "tool_count": len(data.get("tools") or []),
    }

    # Upsert the conversation. If it already exists, fetch its id and
    # delete its old messages so we can re-insert (handles "session got
    # longer" case).
    # NB: project_name is a generated column (split_part(project_path,'/',-1)).
    # We bucket all Hermes sessions under the synthetic project_path "hermes"
    # so they show up as a single "hermes" project in the GUI.
    cur.execute(
        """
        INSERT INTO conversations
            (session_id, project_path, model, entrypoint,
             started_at, ended_at, message_count, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id) DO UPDATE
        SET ended_at = EXCLUDED.ended_at,
            message_count = EXCLUDED.message_count,
            model = COALESCE(EXCLUDED.model, conversations.model),
            metadata = conversations.metadata || EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
        """,
        (
            session_uuid,
            "hermes",
            model,
            platform,
            started,
            ended,
            len(messages),
            Json(conv_metadata),
        ),
    )
    conv_id = cur.fetchone()[0]

    # Clear-and-replace messages for this conversation.
    cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conv_id,))

    inserted = 0
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        hermes_role = (msg.get("role") or "").lower()
        db_role = _ROLE_MAP.get(hermes_role)
        if db_role is None:
            # Unknown role — skip rather than violate the enum.
            continue
        content, blocks = _normalise_content(msg.get("content"))
        meta = _message_metadata(msg)
        msg_uuid = _message_uuid(str(raw_session_id), idx)
        cur.execute(
            """
            INSERT INTO messages
                (conversation_id, uuid, role, content, content_blocks,
                 model, created_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                conv_id,
                msg_uuid,
                db_role,
                content,
                Json(blocks) if blocks else None,
                model if db_role == "assistant" else None,
                started,  # Hermes has no per-message timestamp
                Json(meta) if meta else Json({}),
            ),
        )
        inserted += 1
    return inserted


def main() -> None:
    print("=" * 60)
    print("Throughline — Hermes session ingestion")
    print("=" * 60)

    if not HERMES_DIR.exists():
        print(f"\nNo ~/.hermes/sessions directory found at {HERMES_DIR}.")
        print("Nothing to do.")
        return

    files = sorted(HERMES_DIR.glob("session_*.json"))
    print(f"\nFound: {len(files)} Hermes session file(s)")

    conn = _connect()
    cur = conn.cursor()

    ingested = 0
    skipped = 0
    refreshed = 0
    total_messages = 0
    errors = 0

    try:
        for filepath in files:
            fhash = sha256_file(filepath)

            # ingestion_log idempotency: (file_path, file_hash). If unchanged
            # since last run we skip entirely. If the file's content has
            # changed (hash differs), the previous log row is preserved and a
            # new one is inserted for the new hash.
            cur.execute(
                "SELECT 1 FROM ingestion_log WHERE file_path = %s AND file_hash = %s",
                (str(filepath), fhash),
            )
            if cur.fetchone():
                skipped += 1
                continue

            # Has the file ever been ingested before (under a different hash)?
            cur.execute(
                "SELECT 1 FROM ingestion_log WHERE file_path = %s LIMIT 1",
                (str(filepath),),
            )
            is_refresh = cur.fetchone() is not None

            try:
                n = ingest_session_file(cur, filepath)
                if n == 0:
                    skipped += 1
                    conn.rollback()
                    continue
                cur.execute(
                    "INSERT INTO ingestion_log (file_path, file_hash, record_count) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (str(filepath), fhash, n),
                )
                conn.commit()
                total_messages += n
                if is_refresh:
                    refreshed += 1
                    print(f"  ↻ {filepath.name}: {n} messages (refreshed)")
                else:
                    ingested += 1
                    print(f"  ✓ {filepath.name}: {n} messages")
            except Exception as e:
                conn.rollback()
                errors += 1
                print(f"  ✗ {filepath.name}: {e}")
    finally:
        cur.close()
        conn.close()

    print(f"\n{'=' * 60}")
    print("Result:")
    print(f"  Ingested:    {ingested} new session(s) ({total_messages} messages)")
    print(f"  Refreshed:   {refreshed} updated session(s)")
    print(f"  Skipped:     {skipped} (unchanged)")
    print(f"  Errors:      {errors}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
