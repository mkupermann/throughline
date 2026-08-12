#!/usr/bin/env python3
"""
Generiert prägnante Titel für alle Conversations die noch keinen haben.
Nutzt Claude CLI (kein separater API-Key nötig).
"""
from _bootstrap import use_venv  # noqa: E402
use_venv()


import os

from throughline import llm as _llm
from throughline.self_referential import agent_call_cwd
import sys
import time
from typing import Any

import psycopg2

DB: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "throughline"),
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


def _require_model() -> str:
    """Confirm a model is reachable. `throughline.llm` composes the message."""
    info = _llm.backend_info()
    if not info.available:
        sys.stderr.write(f"ERROR: no model available for titling.\n  {info.detail}\n")
        raise SystemExit(2)
    return str(info)


#: Empty means "whatever the probe found" — the machine's configured model is
#: a property of the machine, not of this script.
MODEL = os.environ.get("THROUGHLINE_TITLE_MODEL", "").strip() or None
MAX_PER_RUN = 50
MAX_PREVIEW_CHARS = 4000
SLEEP = 1.5
TIMEOUT = 60

PROMPT = """Du bekommst einen Auszug aus einer Claude Code Session. Generiere einen prägnanten deutschen Titel (max 60 Zeichen) der den INHALT zusammenfasst.

Regeln:
- Kurz und konkret — kein "Hilfe bei..." oder "Session über..."
- Inhaltsspezifisch: Technologie/Thema nennen
- KEINE Anführungszeichen, KEINE Punkte am Ende
- Format: Nomen-Phrase oder Aktion ("PostgreSQL Memory-DB aufsetzen", "Project Alpha Kickoff-Protokoll")

Beispiele guter Titel:
- "Claude Memory: Schema + Ingestion"
- "Mail Drafter Skill + launchd Scheduler"
- "Project Alpha E2E-Testing Strategie"
- "Diary Automation for Notes App"

Session-Auszug:

{TRANSCRIPT}

Gib NUR den Titel zurück, sonst nichts. Keine Anführungszeichen, keine Erklärung."""


def build_preview(messages: list[tuple[str, str | None]]) -> str:
    """Baut kurzen Transcript-Preview für Titel-Generation."""
    parts = []
    total = 0
    for role, content in messages:
        if role == "tool_result":
            continue
        if not content:
            continue
        text = content[:500] if len(content) > 500 else content
        parts.append(f"[{role}] {text}")
        total += len(text)
        if total > MAX_PREVIEW_CHARS:
            break
    return "\n".join(parts)[:MAX_PREVIEW_CHARS]


def call_model(prompt: str) -> str:
    """Ask whichever backend the probe found for one title. Never raises."""
    try:
        text, err = _llm.complete(
            prompt,
            timeout=TIMEOUT,
            model=MODEL,
            # Run from a directory of our own: Claude Code names the project
            # folder after the process CWD, so inheriting the repo's would file
            # this call inside the user's real project history, and the next
            # ingest would read it back as their work.
            cwd=str(agent_call_cwd()),
        )
        if text is None:
            print(f"  title call failed: {err}")
            return ""
        # Strip the wrapping a chat model adds around a one-line answer.
        title = text.strip()
        title = title.strip('"').strip("'").strip("„").strip("«").strip("»").rstrip(".")
        # First line only, in case the model explained itself.
        title = title.split("\n")[0].strip()
        if len(title) > 80:
            title = title[:77] + "..."
        return title
    except Exception as e:
        print(f"  error: {e}")
        return ""


def main() -> None:
    print("=" * 60)
    print("Claude Memory — Titel-Generierung")
    print("=" * 60)

    print(f"Model: {_require_model()}")
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(f"""
        SELECT id, project_name, message_count
        FROM conversations
        WHERE (summary IS NULL OR summary = '')
          AND message_count >= 2
        ORDER BY started_at DESC
        LIMIT {MAX_PER_RUN}
    """)
    convs = cursor.fetchall()

    print(f"\n{len(convs)} Conversations ohne Titel\n")
    if not convs:
        print("Nichts zu tun.")
        return

    success = 0
    errors = 0

    for conv_id, project, msg_count in convs:
        cursor.execute("""
            SELECT role::text, content FROM messages
            WHERE conversation_id = %s AND role IN ('user', 'assistant')
            ORDER BY created_at
            LIMIT 30
        """, (conv_id,))
        msgs = cursor.fetchall()
        if not msgs:
            continue

        preview = build_preview(msgs)
        if len(preview) < 100:
            continue

        prompt = PROMPT.replace("{TRANSCRIPT}", preview)
        title = call_model(prompt)

        if not title:
            errors += 1
            print(f"  #{conv_id} ({project or '-'}) → FEHLER")
            continue

        cursor.execute("UPDATE conversations SET summary = %s WHERE id = %s", (title, conv_id))
        conn.commit()
        success += 1
        print(f"  #{conv_id} ({project or '-'}): {title}")
        time.sleep(SLEEP)

    print(f"\n{'=' * 60}")
    print(f"Erfolgreich: {success} | Fehler: {errors}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()


#: Kept as an alias: the old name appears in other people's scripts.
call_claude = call_model
