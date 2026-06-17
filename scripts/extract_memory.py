#!/usr/bin/env python3
"""
Memory extraction pipeline.

Two backends are supported, picked at runtime:

- The Anthropic API if ``ANTHROPIC_API_KEY`` is set.
- The Claude Code CLI in headless mode otherwise (``claude -p``), which
  inherits the user's existing CLI authentication and configured model.

Both produce the same JSON shape. By default the transcript is run through
``throughline.pii.redact`` before being sent to Claude — set the environment
variable ``THROUGHLINE_REDACT_PII=0`` to disable.
"""
from _bootstrap import use_venv  # noqa: E402
use_venv()


import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import psycopg2

try:
    from throughline.pii import count_redactions, redact
except ImportError:  # running the script without installing the package
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from throughline.pii import count_redactions, redact

DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

REDACT_PII: bool = os.environ.get("THROUGHLINE_REDACT_PII", "1") != "0"


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


def _claude_present() -> bool:
    """True if the Claude CLI binary can be found (without exiting)."""
    from shutil import which
    b = _resolve_claude_bin()
    return which(b) is not None or os.path.isfile(b)


CLAUDE_BIN = _resolve_claude_bin()
MODEL = "sonnet"
TIMEOUT_OLLAMA = 600  # local 27B model on an 80k-char transcript is slow
EMBED_HINTS = ("embed", "nomic", "bge", "minilm", "gte", "mxbai")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
MAX_CONVERSATIONS_PER_RUN = 20
MIN_MESSAGES = 5
MAX_TRANSCRIPT_CHARS = 80000
# Cap per-message content shown to the extractor. The previous 1,000-char
# cap silently beheaded any long assistant message — multi-axis plans,
# ranked recommendation lists, deep reviews. Anything above ~6 KB used to
# disappear with a "[gekürzt]" marker. The transcript-level
# MAX_TRANSCRIPT_CHARS cap (80,000) already protects against runaway
# prompt growth, so the per-message cap only needs to be wide enough that
# a single richly-structured assistant turn survives intact.
MAX_MESSAGE_CHARS = 8000
# Chunks per conversation. A 300-message session that includes a multi-axis
# plan (e.g. 4 axes × 5 bullets, plus an 8-PR sequence) needs more than 10
# slots if the structure is to be preserved instead of collapsed into a
# generic "project_context" blurb.
MAX_CHUNKS_PER_CONVERSATION = 25
SLEEP_BETWEEN_CALLS = 2.0
# Raised from 120 → 300 s. With MAX_MESSAGE_CHARS at 8,000 the transcript
# can carry richer multi-paragraph assistant turns, which gives the model
# more to read and more to emit (now up to 25 chunks vs 10). The previous
# 2-minute cap was tight for 200+ message sessions; observed timeouts on
# conv #10 (297 msgs) and #46 (210 msgs) zeroed their chunks because the
# clear-before-reextract is committed even when the LLM call gives up.
TIMEOUT_PER_CALL = 300

PROMPT_TEMPLATE = """Du analysierst eine Entwickler-Session (Claude Code, Codex, Hermes, Continue, Windsurf, Cline) und extrahierst verwertbare Erkenntnisse als strukturiertes JSON.

Extrahiere NUR non-obvious Informationen die in FUTURE Sessions nützlich sind:
- decision: Architekturentscheidungen ("Wir nutzen pgvector statt Milvus weil...")
- pattern: Wiederverwendbare Muster ("AppleScript ist schneller mit whose-Filter")
- insight: Überraschende Erkenntnisse ("KeyVault RBAC blockiert App-Zugriff")
- preference: User-Präferenzen ("User bevorzugt Duzen, keine Füllsätze")
- contact: Person/Rolle/Kontext ("Jane Doe = Migration Lead, Project Alpha")
- error_solution: Problem + Lösung ("pg16 + pgvector: selbst kompilieren")
- project_context: Projektwissen ("Project Alpha Summer Release Q2/2026")
- workflow: Abläufe ("launchd-Job: install-schedule.sh install")

WICHTIG — Strukturierte Inhalte erhalten, NICHT zusammenfassen:
Wenn die Session strukturierte Pläne, geordnete Empfehlungslisten oder Mehr-Achsen-
Frameworks enthält (z.B. "Axis A/B/C/D", "Tier 1/2/3", "Free wins / Tier 1 / Tier 2",
"PR 1/8, PR 2/8, …", numerierte Roadmaps, Review-Berichte mit Scores), dann
PRO RANKEDEM ITEM bzw. PRO ACHSE EIN EIGENER CHUNK — nicht alles in eine einzige
"insight"-Zeile zusammenfassen. Erhalte: die ursprüngliche Reihenfolge, ggf. Zeit-
oder Aufwandsschätzungen ("~2 hr"), die Begründung in 1-2 Sätzen, und tagge
einheitlich mit dem Framework-Namen (z.B. tags=["throughline-review","axis-A",
"stunning"]) damit verwandte Chunks später wieder zusammengeführt werden können.

Output: REINES JSON-Array (keine Markdown-Fences, kein Erklärtext), max {MAX_CHUNKS} Chunks.

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
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "...[gekürzt]"
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


# ─── Ollama backend (local, nothing leaves the machine) ─────────────────────
def ollama_url() -> str:
    raw = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_URL") or "http://localhost:11434"
    return raw.rstrip("/")


def ollama_up() -> bool:
    try:
        with urllib.request.urlopen(f"{ollama_url()}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_list_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{ollama_url()}/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def pick_ollama_chat_model(model_names: list[str], preferred: str | None = None) -> str | None:
    """Pick a chat-capable Ollama model.

    Honours ``preferred`` if it is pulled (exact or tag-prefix match), else the
    first model that is not an embedding model. ``None`` if nothing fits.
    """
    names = [n for n in (model_names or []) if n]
    if preferred:
        for n in names:
            if n == preferred or n.startswith(preferred):
                return n
    chat = [n for n in names if not any(h in n.lower() for h in EMBED_HINTS)]
    return chat[0] if chat else None


def _ollama_generate(body: dict, timeout: int) -> str:
    req = urllib.request.Request(
        f"{ollama_url()}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("response", "")


def call_ollama(prompt: str, model: str, timeout: int = TIMEOUT_OLLAMA) -> str:
    """Generate via local Ollama. Disables chain-of-thought for speed (with a
    graceful fallback for older Ollama) and strips any stray <think> block."""
    base = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
    try:
        try:
            raw = _ollama_generate({**base, "think": False}, timeout)
        except urllib.error.HTTPError:
            raw = _ollama_generate(base, timeout)
        return _THINK_RE.sub("", raw).strip()
    except Exception as e:
        print(f"    Ollama exception: {e}")
        return ""


def choose_backend(requested: str, *, ollama_available: bool, claude_available: bool) -> str | None:
    """Resolve the effective backend. ``auto`` prefers local Ollama, then
    Claude. Returns ``None`` when the request can't be satisfied."""
    if requested == "ollama":
        return "ollama" if ollama_available else None
    if requested == "claude":
        return "claude" if claude_available else None
    if ollama_available:
        return "ollama"
    if claude_available:
        return "claude"
    return None


def generate(prompt: str, backend: str, ollama_model: str | None = None) -> str:
    if backend == "ollama":
        return call_ollama(prompt, ollama_model or "")
    return call_claude(prompt)


def extract_for_conversation(cursor: Any, conv_id: int, backend: str = "claude",
                             ollama_model: str | None = None) -> int:
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

    if REDACT_PII:
        redacted = redact(transcript)
        n = count_redactions(transcript, redacted)
        if n:
            print(f"    redacted {n} secret/PII match(es) before extraction")
        transcript = redacted

    prompt = (
        PROMPT_TEMPLATE
        .replace("{MAX_CHUNKS}", str(MAX_CHUNKS_PER_CONVERSATION))
        .replace("{TRANSCRIPT}", transcript)
    )
    response = generate(prompt, backend, ollama_model)
    if not response:
        return 0

    chunks = parse_json_response(response)
    # Cap defensively in case the model ignores the prompt — extra chunks are
    # truncated rather than rejected, so we never silently drop a session.
    if len(chunks) > MAX_CHUNKS_PER_CONVERSATION:
        chunks = chunks[:MAX_CHUNKS_PER_CONVERSATION]
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


def _parse_id_list(raw: str) -> list[int]:
    """Parse a comma-separated id list like '10,15,47' into [10,15,47]."""
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise SystemExit(f"--force-conversations: '{part}' is not an integer")
    return out


def _force_reextract(cursor, conv_ids: list[int], backend: str = "claude",
                     ollama_model: str | None = None) -> tuple[int, int, int]:
    """Delete previous chunks for the given conversations and re-extract.

    Returns ``(deleted, inserted, errors)``. Each conversation is processed
    in its own transaction at the caller's commit() boundary, so a failure
    on one ID doesn't roll back the others.
    """
    deleted_total = 0
    inserted_total = 0
    errors = 0
    for cid in conv_ids:
        cursor.execute(
            "DELETE FROM memory_chunks "
            "WHERE source_type='conversation' AND source_id=%s "
            "RETURNING id",
            (cid,),
        )
        deleted = len(cursor.fetchall())
        deleted_total += deleted
        print(f"  #{cid} re-extract (cleared {deleted} old chunk(s))", end=" ", flush=True)
        try:
            n = extract_for_conversation(cursor, cid, backend, ollama_model)
            inserted_total += n
            print(f"→ {n} Chunks")
        except Exception as e:
            errors += 1
            print(f"✗ {e}")
            raise
    return deleted_total, inserted_total, errors


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract memory chunks from ingested conversations."
    )
    parser.add_argument(
        "--force-conversations",
        metavar="IDS",
        help="Comma-separated conversation IDs to re-extract. Deletes their "
             "existing memory_chunks and runs extraction again with the "
             "current prompt and limits. Use this after changing the "
             "extractor to refresh affected rows.",
    )
    parser.add_argument("--backend", choices=["auto", "ollama", "claude"], default="auto",
                        help="Extraction backend. auto = local Ollama first, Claude as fallback.")
    parser.add_argument("--ollama-model", default=(os.environ.get("THROUGHLINE_OLLAMA_CHAT_MODEL")
                                                   or os.environ.get("OLLAMA_CHAT_MODEL")),
                        help="Force a specific Ollama chat model (otherwise auto-detected).")
    args = parser.parse_args()

    print("=" * 60)
    print("Throughline — Memory Extraction")
    print("=" * 60)

    models = ollama_list_models() if ollama_up() else []
    ollama_model = pick_ollama_chat_model(models, preferred=args.ollama_model)
    backend = choose_backend(args.backend, ollama_available=ollama_model is not None,
                             claude_available=_claude_present())
    if backend is None:
        sys.stderr.write(
            "ERROR: Kein nutzbares Backend.\n"
            f"  Ollama erreichbar: {ollama_up()} | Chat-Modell: {ollama_model or '—'}\n"
            f"  Claude CLI vorhanden: {_claude_present()}\n"
            "  Ziehe ein Ollama-Chat-Modell (z.B. `ollama pull qwen3`) oder installiere die Claude CLI.\n"
        )
        raise SystemExit(2)
    if backend == "claude":
        _require_claude_bin()

    if backend == "ollama":
        print(f"Backend: ollama  Modell: {ollama_model}  ({ollama_url()})  PII-Redaktion: {REDACT_PII}")
    else:
        print(f"Backend: claude  Modell: {MODEL}  PII-Redaktion: {REDACT_PII}")

    conn = _connect()
    cursor = conn.cursor()

    if args.force_conversations:
        conv_ids = _parse_id_list(args.force_conversations)
        print(f"\nForce-re-extracting {len(conv_ids)} conversation(s): {conv_ids}\n")
        try:
            deleted, inserted, errors = _force_reextract(cursor, conv_ids, backend, ollama_model)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"\n{'=' * 60}")
        print(f"Re-extracted: {len(conv_ids)} | Old chunks cleared: {deleted} | "
              f"New chunks: {inserted} | Errors: {errors}")
        print(f"{'=' * 60}")
        cursor.close()
        conn.close()
        return

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
            n = extract_for_conversation(cursor, conv_id, backend, ollama_model)
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
