#!/usr/bin/env python3
"""
Embeddings-Generator für claude_memory DB.

Unterstützt zwei Backends:
  --backend openai   -> OpenAI text-embedding-3-small (1536 dim)   [OPENAI_API_KEY]
  --backend ollama   -> Ollama nomic-embed-text (768 dim)          [lokal]

Generiert Embeddings für:
  - ALLE memory_chunks
  - Messages mit role IN ('user','assistant') UND length(content) >= 100

Schreibt in Tabelle `embeddings` mit separaten Spalten:
  embedding_1536 (vector(1536))   -> OpenAI
  embedding_768  (vector(768))    -> Ollama

Upsert-Strategie: Zeilen-Key ist (source_type, source_id, model).
Skippt automatisch bereits embeddete Einträge.

Usage:
    python3 generate_embeddings.py --backend openai
    python3 generate_embeddings.py --backend ollama
    python3 generate_embeddings.py --backend auto   # OpenAI wenn key da, sonst Ollama
    python3 generate_embeddings.py --backend ollama --limit 200
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import List, Sequence

import psycopg2

DB_CONFIG = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

OPENAI_MODEL = "text-embedding-3-small"
OPENAI_DIM = 1536
OPENAI_URL = "https://api.openai.com/v1/embeddings"
OPENAI_BATCH = 100
OPENAI_MAX_CHARS = 24000  # rough safety cap per input

OLLAMA_MODEL = "nomic-embed-text"
OLLAMA_DIM = 768
OLLAMA_URL = "http://localhost:11434"
OLLAMA_BATCH = 1
OLLAMA_MAX_CHARS = 4000

MIN_MESSAGE_CHARS = 100


# ─── Backend ──────────────────────────────────────────────────────────────────
class Backend:
    name: str
    model: str
    dim: int
    column: str
    batch: int
    max_chars: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError


class OpenAIBackend(Backend):
    name = "openai"
    model = OPENAI_MODEL
    dim = OPENAI_DIM
    column = "embedding_1536"
    batch = OPENAI_BATCH
    max_chars = OPENAI_MAX_CHARS

    def __init__(self, api_key: str):
        self.api_key = api_key

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        payload = json.dumps({
            "model": self.model,
            "input": list(texts),
        }).encode("utf-8")
        req = urllib.request.Request(
            OPENAI_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode("utf-8"))
        return [item["embedding"] for item in body["data"]]


class OllamaBackend(Backend):
    name = "ollama"
    model = OLLAMA_MODEL
    dim = OLLAMA_DIM
    column = "embedding_768"
    batch = OLLAMA_BATCH
    max_chars = OLLAMA_MAX_CHARS

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for t in texts:
            payload = json.dumps({"model": self.model, "prompt": t}).encode("utf-8")
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read().decode("utf-8"))
            out.append(body["embedding"])
        return out


# ─── Backend-Erkennung ────────────────────────────────────────────────────────
def ollama_up() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_has_model(model: str) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return any(m.get("name", "").startswith(model) for m in data.get("models", []))
    except Exception:
        return False


def ollama_pull(model: str) -> bool:
    try:
        payload = json.dumps({"name": model, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status == 200
    except Exception as e:
        print(f"  ollama pull fehlgeschlagen: {e}")
        return False


def pick_backend(choice: str) -> Backend:
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if choice in ("openai", "auto") and openai_key:
        if choice == "auto":
            print("Backend: openai (OPENAI_API_KEY gefunden)")
        return OpenAIBackend(openai_key)
    if choice == "openai":
        sys.stderr.write(
            "FEHLER: --backend openai gewählt, aber OPENAI_API_KEY nicht gesetzt.\n"
            "  Setze: export OPENAI_API_KEY=sk-...\n"
            "  Oder nutze: --backend ollama (lokal, kostenlos)\n"
        )
        sys.exit(2)

    # Ollama-Pfad
    if not ollama_up():
        sys.stderr.write(
            "FEHLER: Ollama läuft nicht auf http://localhost:11434\n"
            "  Start:  brew services start ollama   oder   ollama serve\n"
            "  Install: brew install ollama\n"
        )
        if choice == "auto":
            sys.stderr.write("  (Alternativ: export OPENAI_API_KEY=sk-... setzen)\n")
        sys.exit(2)
    if not ollama_has_model(OLLAMA_MODEL):
        print(f"Ollama-Modell '{OLLAMA_MODEL}' fehlt — ziehe es jetzt…")
        if not ollama_pull(OLLAMA_MODEL):
            sys.stderr.write(
                f"FEHLER: konnte Modell '{OLLAMA_MODEL}' nicht ziehen.\n"
                f"  Manuell:  ollama pull {OLLAMA_MODEL}\n"
            )
            sys.exit(2)
    if choice == "auto":
        print("Backend: ollama (OPENAI_API_KEY fehlt, Ollama verfügbar)")
    return OllamaBackend()


# ─── DB helpers ───────────────────────────────────────────────────────────────
def fetch_pending(cursor, backend: Backend, limit: int | None) -> list:
    """
    Liefert (source_type, source_id, content) aller Zeilen,
    für die noch KEIN Embedding mit dem gegebenen Modell existiert.
    """
    lim_sql = f"LIMIT {int(limit)}" if limit else ""
    cursor.execute(f"""
        SELECT 'memory_chunk' AS source_type, mc.id, mc.content
        FROM memory_chunks mc
        WHERE NOT EXISTS (
            SELECT 1 FROM embeddings e
            WHERE e.source_type = 'memory_chunk' AND e.source_id = mc.id
              AND e.model = %s AND e.{backend.column} IS NOT NULL
        )
        UNION ALL
        SELECT 'message' AS source_type, m.id, m.content
        FROM messages m
        WHERE m.role IN ('user','assistant')
          AND m.content IS NOT NULL
          AND length(m.content) >= %s
          AND NOT EXISTS (
              SELECT 1 FROM embeddings e
              WHERE e.source_type = 'message' AND e.source_id = m.id
                AND e.model = %s AND e.{backend.column} IS NOT NULL
          )
        {lim_sql}
    """, (backend.model, MIN_MESSAGE_CHARS, backend.model))
    return cursor.fetchall()


def upsert_embedding(cursor, backend: Backend, source_type: str, source_id: int, vector: list):
    vec_literal = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
    # INSERT … ON CONFLICT auf (source_type, source_id, model)
    sql = f"""
        INSERT INTO embeddings (source_type, source_id, model, {backend.column})
        VALUES (%s, %s, %s, %s::vector)
        ON CONFLICT (source_type, source_id, model)
        DO UPDATE SET {backend.column} = EXCLUDED.{backend.column},
                      created_at = now()
    """
    cursor.execute(sql, (source_type, source_id, backend.model, vec_literal))


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["openai", "ollama", "auto"], default="auto")
    ap.add_argument("--limit", type=int, default=None, help="nur N Einträge verarbeiten (Test)")
    ap.add_argument("--only", choices=["memory_chunk", "message", "both"], default="both")
    args = ap.parse_args()

    backend = pick_backend(args.backend)
    print(f"Backend: {backend.name} / {backend.model} ({backend.dim} dim)")

    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cursor = conn.cursor()

    pending = fetch_pending(cursor, backend, args.limit)
    if args.only != "both":
        pending = [p for p in pending if p[0] == args.only]
    total = len(pending)
    print(f"Zu verarbeiten: {total} Einträge\n")
    if total == 0:
        print("Nichts zu tun.")
        return

    done = 0
    errors = 0
    batch_sz = backend.batch

    for start in range(0, total, batch_sz):
        batch = pending[start:start + batch_sz]
        texts = []
        meta = []
        for stype, sid, content in batch:
            if not content:
                continue
            t = content[: backend.max_chars]
            texts.append(t)
            meta.append((stype, sid))

        if not texts:
            continue

        try:
            vectors = backend.embed(texts)
        except urllib.error.HTTPError as he:
            errors += 1
            body = ""
            try:
                body = he.read().decode("utf-8")[:300]
            except Exception:
                pass
            print(f"  HTTP {he.code} bei Batch ab {start}: {body}")
            time.sleep(2)
            continue
        except Exception as e:
            errors += 1
            print(f"  Embedding-Fehler bei Batch ab {start}: {type(e).__name__}: {e}")
            time.sleep(1)
            continue

        if len(vectors) != len(meta):
            print(f"  WARN: vectors={len(vectors)} vs meta={len(meta)} – skip")
            continue

        try:
            for (stype, sid), vec in zip(meta, vectors):
                if len(vec) != backend.dim:
                    print(f"  WARN: dim mismatch {len(vec)} != {backend.dim} bei {stype}#{sid}")
                    continue
                upsert_embedding(cursor, backend, stype, sid, vec)
            conn.commit()
        except Exception as e:
            conn.rollback()
            errors += 1
            print(f"  DB-Fehler bei Batch ab {start}: {e}")
            continue

        done += len(meta)
        pct = 100.0 * done / total
        print(f"  {done}/{total} ({pct:.1f}%) eingebettet")

    print(f"\nFertig. done={done} errors={errors}")
    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
