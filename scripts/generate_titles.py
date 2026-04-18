#!/usr/bin/env python3
"""
Generiert prägnante Titel für alle Conversations die noch keinen haben.
Nutzt Claude CLI (kein separater API-Key nötig).
"""

import os
import subprocess
import sys
import time
import psycopg2

DB = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}


def _resolve_claude_bin() -> str:
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    from shutil import which
    found = which("claude")
    return found or "claude"


CLAUDE_BIN = _resolve_claude_bin()
MODEL = "sonnet"
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


def build_preview(messages: list) -> str:
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


def call_claude(prompt: str) -> str:
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--model", MODEL],
            capture_output=True, text=True, timeout=TIMEOUT
        )
        if r.returncode != 0:
            return ""
        # Bereinige Output
        title = r.stdout.strip()
        # Anführungszeichen entfernen
        title = title.strip('"').strip("'").strip("„").strip("«").strip("»").rstrip(".")
        # Erste Zeile nehmen falls mehrere
        title = title.split("\n")[0].strip()
        # Max 80 Zeichen
        if len(title) > 80:
            title = title[:77] + "..."
        return title
    except Exception as e:
        print(f"  Fehler: {e}")
        return ""


def main():
    print("=" * 60)
    print("Claude Memory — Titel-Generierung")
    print("=" * 60)

    conn = psycopg2.connect(**DB)
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
        title = call_claude(prompt)

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
