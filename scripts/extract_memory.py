#!/usr/bin/env python3
"""
Memory-Extraction Pipeline via Claude Code CLI (kein API-Key nötig).
Nutzt `claude -p` headless mode mit dem Max Plan des Users.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any

import psycopg2

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


def _resolve_claude_bin() -> str:
    """Find the `claude` CLI on PATH or via the CLAUDE_BIN env var.

    Falls back to the literal string "claude" so users relying on PATH still work.
    """
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    from shutil import which
    found = which("claude")
    return found or "claude"


def _require_claude_bin() -> str:
    """Resolve the Claude CLI binary or emit a clear error and exit."""
    bin_path = _resolve_claude_bin()
    from shutil import which
    if which(bin_path) is None and not os.path.isfile(bin_path):
        sys.stderr.write(
            "ERROR: Claude CLI not found.\n"
            "  Set $CLAUDE_BIN or install the Claude Code CLI:\n"
            "    https://docs.anthropic.com/en/docs/claude-code/setup\n"
        )
        raise SystemExit(2)
    return bin_path


CLAUDE_BIN = _resolve_claude_bin()
MODEL = "sonnet"
MAX_CONVERSATIONS_PER_RUN = 20
MIN_MESSAGES = 5
MAX_TRANSCRIPT_CHARS = 80000
SLEEP_BETWEEN_CALLS = 2.0
TIMEOUT_PER_CALL = 120

PROMPT_TEMPLATE = """Du analysierst eine Claude Code Entwickler-Session und extrahierst verwertbare Erkenntnisse als strukturiertes JSON.

Extrahiere NUR non-obvious Informationen die in FUTURE Sessions nützlich sind:
- decision: Architekturentscheidungen ("Wir nutzen pgvector statt Milvus weil...")
- pattern: Wiederverwendbare Muster ("AppleScript ist schneller mit whose-Filter")
- insight: Überraschende Erkenntnisse ("KeyVault RBAC blockiert App-Zugriff")
- preference: User-Präferenzen ("User bevorzugt Duzen, keine Füllsätze")
- contact: Person/Rolle/Kontext ("Jane Doe = Migration Lead, Project Alpha")
- error_solution: Problem + Lösung ("pg16 + pgvector: selbst kompilieren")
- project_context: Projektwissen ("Project Alpha Summer Release Q2/2026")
- workflow: Abläufe ("launchd-Job: install-schedule.sh install")

Ignoriere: Triviales, normales Code-Schreiben, allgemeine Fragen, Smalltalk.

Output: REINES JSON-Array (keine Markdown-Fences, kein Erklärtext), max 10 Chunks.

Format:
[
  {"content": "...", "category": "decision", "tags": ["postgresql", "pgvector"], "confidence": 0.9, "project": "claude-memory-db"}
]

Wenn nichts Verwertbares: []

Transcript:

{TRANSCRIPT}

Gib NUR das JSON-Array zurück, nichts anderes."""


def build_transcript(messages: list[tuple[str, str | None]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m[0]
        content = m[1] or ""
        if role == "tool_result":
            continue
        if len(content) > 1000:
            content = content[:1000] + "...[gekürzt]"
        parts.append(f"[{role.upper()}]\n{content}\n")
    transcript = "\n".join(parts)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
    return transcript


def parse_json_response(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start:end+1])
    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}")
        return []


def call_claude(prompt: str) -> str:
    """Ruft claude CLI headless auf. Gibt Text-Output zurück."""
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--model", MODEL],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_CALL,
        )
        if result.returncode != 0:
            print(f"    Claude CLI error (exit {result.returncode}): {result.stderr[:200]}")
            return ""
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"    Claude CLI timeout ({TIMEOUT_PER_CALL}s)")
        return ""
    except Exception as e:
        print(f"    Claude CLI exception: {e}")
        return ""


def extract_for_conversation(cursor: Any, conv_id: int) -> int:
    cursor.execute("""
        SELECT role::text, content
        FROM messages
        WHERE conversation_id = %s AND role IN ('user', 'assistant')
        ORDER BY created_at
    """, (conv_id,))
    rows = cursor.fetchall()
    if not rows:
        return 0

    transcript = build_transcript(rows)
    if len(transcript) < 200:
        return 0

    prompt = PROMPT_TEMPLATE.replace("{TRANSCRIPT}", transcript)
    response = call_claude(prompt)
    if not response:
        return 0

    chunks = parse_json_response(response)
    inserted = 0
    for chunk in chunks:
        try:
            content = chunk.get("content", "").strip()
            category = chunk.get("category", "insight")
            tags = chunk.get("tags", [])
            confidence = float(chunk.get("confidence", 0.8))
            project = chunk.get("project") or None
            if not content or category not in ["decision", "pattern", "insight", "preference", "contact", "error_solution", "project_context", "workflow"]:
                continue
            cursor.execute("""
                INSERT INTO memory_chunks (source_type, source_id, content, category, tags, confidence, project_name)
                VALUES ('conversation', %s, %s, %s, %s, %s, %s)
            """, (conv_id, content, category, tags, confidence, project))
            inserted += 1
        except Exception as e:
            print(f"    Insert-Fehler: {e}")
            continue

    return inserted


def main() -> None:
    print("=" * 60)
    print("Claude Memory DB — Memory Extraction (via Claude CLI)")
    print("=" * 60)

    _require_claude_bin()
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(f"""
        SELECT c.id, c.project_name, c.message_count
        FROM conversations c
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_chunks mc
            WHERE mc.source_type = 'conversation' AND mc.source_id = c.id
        )
        AND c.message_count >= {MIN_MESSAGES}
        ORDER BY c.started_at DESC
        LIMIT {MAX_CONVERSATIONS_PER_RUN}
    """)
    convs = cursor.fetchall()

    print(f"\n{len(convs)} Conversations zu analysieren\n")
    if not convs:
        print("Nichts zu tun.")
        return

    total_chunks = 0
    errors = 0

    for conv_id, project_name, msg_count in convs:
        print(f"  #{conv_id} ({project_name or '–'}, {msg_count} Msgs)", end=" ", flush=True)
        try:
            n = extract_for_conversation(cursor, conv_id)
            conn.commit()
            total_chunks += n
            print(f"→ {n} Chunks")
        except Exception as e:
            conn.rollback()
            errors += 1
            print(f"✗ {e}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    print(f"\n{'=' * 60}")
    print(f"Analysiert: {len(convs)} | Chunks: {total_chunks} | Fehler: {errors}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
