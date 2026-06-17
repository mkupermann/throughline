#!/usr/bin/env python3
"""
Erzeugt klare deutsche Beschreibungen für Conversations: Anfang, was gemacht
wurde, was erreicht wurde.

Anders als eine reine Titelzeile liest dieser Generator die ganze Session
(Anfang, Mitte und Ende werden gesampelt, nicht nur die ersten Nachrichten),
damit lange Sessions nicht nur über ihren Einstieg beschrieben werden.

Backends, in dieser Reihenfolge bei ``--backend auto`` (Default):
  1. Ollama (lokal, nichts verlässt den Rechner) — wenn ein Chat-Modell da ist.
  2. Claude CLI (Fallback) — braucht das installierte ``claude``-Binary.
Erzwingen mit ``--backend ollama`` bzw. ``--backend claude``.
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

DB: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

# ─── Tunables ──────────────────────────────────────────────────────────────
MODEL = "sonnet"            # Claude model (fallback backend)
MAX_PER_RUN = 50
MAX_PREVIEW_CHARS = 6000    # transcript budget handed to the model
MAX_MSG_CHARS = 500         # per-message truncation before sampling
MAX_SUMMARY_CHARS = 800     # cap on the stored description
SLEEP_CLAUDE = 1.5
SLEEP_OLLAMA = 0.2
TIMEOUT_CLAUDE = 60
TIMEOUT_OLLAMA = 240        # local models are slower, especially on long input

# Model-name fragments that mark an embedding model (never a chat model).
EMBED_HINTS = ("embed", "nomic", "bge", "minilm", "gte", "mxbai")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

PROMPT = """Du bekommst Auszüge aus einer Entwickler-Session mit einem KI-Assistenten. Bei langen Sessions sind nur Anfang, Mitte und Ende enthalten, die Markierungen zeigen wo Teile ausgelassen sind.

Schreibe eine klare deutsche Beschreibung in zwei bis vier Sätzen, als ein Absatz, die drei Dinge abdeckt:
1. Womit die Session begann, also Ausgangslage oder Ziel.
2. Was gemacht wurde, also die wichtigsten Schritte, Technologien und Dateien.
3. Was am Ende erreicht wurde, also Ergebnis oder Stand.

Regeln:
- Konkret und sachlich, nenne Technologien und Themen beim Namen.
- Kein Vorwort wie "In dieser Session", keine Aufzählung, keine Anführungszeichen.
- Ein zusammenhängender Absatz, deutscher Fließtext.

Session-Auszüge:

{TRANSCRIPT}

Gib NUR die Beschreibung zurück, sonst nichts."""


# ─── DB ──────────────────────────────────────────────────────────────────────
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


# ─── Claude backend ───────────────────────────────────────────────────────────
def _resolve_claude_bin() -> str:
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    from shutil import which
    found = which("claude")
    return found or "claude"


CLAUDE_BIN = _resolve_claude_bin()


def _claude_present() -> bool:
    """True if the Claude CLI binary can be found (without exiting)."""
    from shutil import which
    return which(CLAUDE_BIN) is not None or os.path.isfile(CLAUDE_BIN)


def _require_claude_bin() -> str:
    """Resolve the Claude CLI binary or emit a clear error and exit."""
    if not _claude_present():
        sys.stderr.write(
            "ERROR: Claude CLI not found.\n"
            "  Set $CLAUDE_BIN or install the Claude Code CLI:\n"
            "    https://docs.anthropic.com/en/docs/claude-code/setup\n"
        )
        raise SystemExit(2)
    return CLAUDE_BIN


def call_claude(prompt: str) -> str:
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--model", MODEL],
            capture_output=True, text=True, timeout=TIMEOUT_CLAUDE
        )
        if r.returncode != 0:
            return ""
        return r.stdout
    except Exception as e:
        print(f"  Fehler (claude): {e}")
        return ""


# ─── Ollama backend (local) ────────────────────────────────────────────────
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

    Honours ``preferred`` if it is actually pulled (exact or tag-prefix match),
    otherwise returns the first model that is not an embedding model. Returns
    ``None`` when nothing chat-capable is available.
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
    # Disable the model's chain-of-thought: a summary needs no reasoning trace,
    # and on a "thinking" model like qwen3 it is the difference between seconds
    # and minutes per call. Fall back gracefully on older Ollama that rejects
    # the `think` field, and strip any stray <think> block either way.
    base = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
    try:
        try:
            raw = _ollama_generate({**base, "think": False}, timeout)
        except urllib.error.HTTPError:
            raw = _ollama_generate(base, timeout)
        return _THINK_RE.sub("", raw)
    except Exception as e:
        print(f"  Fehler (ollama): {e}")
        return ""


def choose_backend(requested: str, *, ollama_available: bool, claude_available: bool) -> str | None:
    """Resolve the effective backend. ``auto`` prefers local Ollama, falls back
    to Claude. Returns ``None`` when the request cannot be satisfied."""
    if requested == "ollama":
        return "ollama" if ollama_available else None
    if requested == "claude":
        return "claude" if claude_available else None
    # auto
    if ollama_available:
        return "ollama"
    if claude_available:
        return "claude"
    return None


def generate(prompt: str, backend: str, ollama_model: str | None = None) -> str:
    if backend == "ollama":
        return call_ollama(prompt, ollama_model or "")
    return call_claude(prompt)


# ─── Transcript preview + cleanup (pure) ─────────────────────────────────────
def _clean_messages(messages: list[tuple[str, str | None]]) -> list[str]:
    parts = []
    for role, content in messages:
        if role == "tool_result" or not content:
            continue
        parts.append(f"[{role}] {content[:MAX_MSG_CHARS]}")
    return parts


def build_preview(messages: list[tuple[str, str | None]], max_chars: int = MAX_PREVIEW_CHARS) -> str:
    """Build a transcript preview that reflects the WHOLE session.

    Short sessions are returned in full. Long sessions are sampled: a slice
    from the beginning, one from the middle and one from the end, with markers
    where content was dropped — so a summary describes the whole arc, not just
    the opening. Never exceeds ``max_chars``.
    """
    parts = _clean_messages(messages)
    if not parts:
        return ""
    full = "\n".join(parts)
    if len(full) <= max_chars:
        return full

    sep_mid = "\n\n[… Mitte der Session ausgelassen …]\n\n"
    sep_end = "\n\n[… gegen Ende der Session …]\n\n"
    budget = max_chars - len(sep_mid) - len(sep_end)
    if budget < 3:
        return full[:max_chars]

    head_n = int(budget * 0.4)
    tail_n = int(budget * 0.4)
    mid_n = budget - head_n - tail_n
    head = full[:head_n]
    tail = full[len(full) - tail_n:]
    mid_start = max(head_n, len(full) // 2 - mid_n // 2)
    mid = full[mid_start: mid_start + mid_n]
    return head + sep_mid + mid + sep_end + tail


def clean_summary(raw: str) -> str:
    """Normalise a model response into a single-paragraph description."""
    s = (raw or "").strip()
    s = _THINK_RE.sub("", s).strip()
    quote_pairs = [('"', '"'), ("'", "'"), ("„", "“"), ("«", "»"), ("»", "«"), ("“", "”")]
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for a, b in quote_pairs:
            if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b) - 1:
                s = s[len(a):len(s) - len(b)].strip()
                changed = True
    s = " ".join(s.split())
    if len(s) > MAX_SUMMARY_CHARS:
        s = s[:MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return s


# ─── Main ────────────────────────────────────────────────────────────────────
def _parse_args(argv: list[str] | None = None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Erzeugt klare Beschreibungen (Anfang / was gemacht / Ergebnis) für Conversations."
    )
    ap.add_argument("--backend", choices=["auto", "ollama", "claude"], default="auto",
                    help="Backend-Auswahl. auto = erst Ollama lokal, sonst Claude.")
    ap.add_argument("--ollama-model", default=(os.environ.get("THROUGHLINE_OLLAMA_CHAT_MODEL")
                                               or os.environ.get("OLLAMA_CHAT_MODEL")),
                    help="Ollama-Chat-Modell erzwingen (sonst automatisch erkannt).")
    ap.add_argument("--force", action="store_true",
                    help="Auch vorhandene Beschreibungen überschreiben.")
    ap.add_argument("--conversation", type=int, action="append",
                    help="Nur diese Conversation-ID(s). Mehrfach möglich.")
    ap.add_argument("--project", help="Nur Conversations dieses Projekts (project_name).")
    ap.add_argument("--limit", type=int, default=MAX_PER_RUN,
                    help=f"Maximale Anzahl pro Lauf (Default {MAX_PER_RUN}).")
    return ap.parse_args(argv)


def main() -> int:
    args = _parse_args()

    print("=" * 60)
    print("Throughline — Conversation-Beschreibungen")
    print("=" * 60)

    models = ollama_list_models() if ollama_up() else []
    ollama_model = pick_ollama_chat_model(models, preferred=args.ollama_model)
    ollama_available = ollama_model is not None
    claude_available = _claude_present()

    backend = choose_backend(args.backend, ollama_available=ollama_available,
                             claude_available=claude_available)
    if backend is None:
        sys.stderr.write(
            "ERROR: Kein nutzbares Backend.\n"
            f"  Ollama erreichbar: {ollama_up()} | Chat-Modell: {ollama_model or '—'}\n"
            f"  Claude CLI vorhanden: {claude_available}\n"
            "  Ziehe ein Ollama-Chat-Modell (z.B. `ollama pull qwen3`) oder installiere die Claude CLI.\n"
        )
        return 2
    if backend == "claude":
        _require_claude_bin()

    if backend == "ollama":
        print(f"Backend: ollama  Modell: {ollama_model}  ({ollama_url()})")
    else:
        print(f"Backend: claude  Modell: {MODEL}")

    conn = _connect()
    cursor = conn.cursor()

    where = ["message_count >= 2"]
    params: list[Any] = []
    if not args.force:
        where.append("(summary IS NULL OR summary = '')")
    if args.conversation:
        where.append("id = ANY(%s)")
        params.append(args.conversation)
    if args.project:
        where.append("project_name = %s")
        params.append(args.project)
    params.append(args.limit)

    cursor.execute(
        f"SELECT id, project_name, message_count FROM conversations "
        f"WHERE {' AND '.join(where)} ORDER BY message_count DESC LIMIT %s",
        params,
    )
    convs = cursor.fetchall()

    verb = "neu zu beschreiben" if not args.force else "zu (über)schreiben"
    print(f"\n{len(convs)} Conversations {verb}\n")
    if not convs:
        print("Nichts zu tun.")
        cursor.close()
        conn.close()
        return 0

    sleep = SLEEP_OLLAMA if backend == "ollama" else SLEEP_CLAUDE
    success = 0
    errors = 0

    for conv_id, project, msg_count in convs:
        cursor.execute(
            "SELECT role::text, content FROM messages "
            "WHERE conversation_id = %s AND role IN ('user', 'assistant') "
            "ORDER BY created_at",
            (conv_id,),
        )
        msgs = cursor.fetchall()
        if not msgs:
            continue

        preview = build_preview(msgs)
        if len(preview) < 100:
            continue

        prompt = PROMPT.replace("{TRANSCRIPT}", preview)
        summary = clean_summary(generate(prompt, backend, ollama_model))

        if not summary:
            errors += 1
            print(f"  #{conv_id} ({project or '-'}, {msg_count} msgs) → FEHLER")
            continue

        cursor.execute("UPDATE conversations SET summary = %s WHERE id = %s", (summary, conv_id))
        conn.commit()
        success += 1
        print(f"  #{conv_id} ({project or '-'}, {msg_count} msgs):\n      {summary}")
        time.sleep(sleep)

    print(f"\n{'=' * 60}")
    print(f"Erfolgreich: {success} | Fehler: {errors}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
