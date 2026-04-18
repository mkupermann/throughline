#!/usr/bin/env python3
"""
Semantic Search über claude_memory DB via pgvector cosine distance.

Usage:
    python3 search_semantic.py "pgvector postgres"
    python3 search_semantic.py "Project Alpha migration" --limit 10 --backend ollama
    python3 search_semantic.py "Jane Doe" --backend auto

Gibt gruppierte Ergebnisse nach source_type (memory_chunk, message) aus.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import List

import psycopg2
import psycopg2.extras

# Re-use helpers from generate_embeddings if importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from generate_embeddings import (  # type: ignore
    DB_CONFIG,
    pick_backend,
    Backend,
)


def embed_query(backend: Backend, query: str) -> List[float]:
    vec = backend.embed([query[: backend.max_chars]])[0]
    if len(vec) != backend.dim:
        raise RuntimeError(f"Unerwartete Embedding-Dim: {len(vec)} != {backend.dim}")
    return vec


def vec_literal(vec: List[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def run_search(cursor, backend: Backend, query: str, limit: int):
    vec = embed_query(backend, query)
    v_lit = vec_literal(vec)
    col = backend.column

    sql = f"""
        WITH mc AS (
            SELECT 'memory_chunk'::text AS source_type,
                   mc.id AS source_id,
                   mc.content,
                   mc.category::text AS category,
                   mc.project_name,
                   mc.confidence::float AS confidence,
                   e.{col} <=> %s::vector AS distance
            FROM embeddings e
            JOIN memory_chunks mc ON mc.id = e.source_id
            WHERE e.source_type = 'memory_chunk'
              AND e.model = %s
              AND e.{col} IS NOT NULL
        ),
        ms AS (
            SELECT 'message'::text AS source_type,
                   m.id AS source_id,
                   m.content,
                   m.role::text AS category,
                   c.project_name,
                   NULL::float AS confidence,
                   e.{col} <=> %s::vector AS distance
            FROM embeddings e
            JOIN messages m ON m.id = e.source_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE e.source_type = 'message'
              AND e.model = %s
              AND e.{col} IS NOT NULL
        )
        SELECT * FROM (
            SELECT * FROM mc
            UNION ALL
            SELECT * FROM ms
        ) x
        ORDER BY distance ASC
        LIMIT %s
    """
    cursor.execute(sql, (v_lit, backend.model, v_lit, backend.model, limit))
    return cursor.fetchall()


def fmt_score(d: float) -> str:
    """cosine distance in [0,2] -> similarity style."""
    sim = 1.0 - d
    return f"dist={d:.3f}  sim={sim:.3f}"


def trunc(s: str, n: int = 280) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="Suchbegriff")
    ap.add_argument("--backend", choices=["openai", "ollama", "auto"], default="auto")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    backend = pick_backend(args.backend)

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Sanity: gibt es überhaupt Embeddings mit diesem Modell?
    cur.execute("SELECT COUNT(*) AS n FROM embeddings WHERE model = %s", (backend.model,))
    n_total = cur.fetchone()["n"]
    if n_total == 0:
        sys.stderr.write(
            f"Keine Embeddings mit Modell '{backend.model}' in der DB.\n"
            f"Erst generieren:  python3 {SCRIPT_DIR}/generate_embeddings.py --backend {backend.name}\n"
        )
        sys.exit(3)

    rows = run_search(cur, backend, args.query, args.limit)
    if not rows:
        print("Keine Treffer.")
        return

    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["source_type"], []).append(r)

    print(f"\n=== Semantic Search: {args.query!r} ===")
    print(f"Backend: {backend.name} / {backend.model}  |  Basis: {n_total} Embeddings  |  Treffer: {len(rows)}\n")

    for stype in ("memory_chunk", "message"):
        if stype not in grouped:
            continue
        label = "💡 Memory Chunks" if stype == "memory_chunk" else "📝 Messages"
        print(f"--- {label} ({len(grouped[stype])}) ---")
        for i, r in enumerate(grouped[stype], 1):
            meta = [r.get("category") or ""]
            if r.get("project_name"):
                meta.append(r["project_name"])
            if r.get("confidence") is not None:
                try:
                    meta.append(f"conf={float(r['confidence']):.2f}")
                except Exception:
                    pass
            meta_str = " | ".join(m for m in meta if m)
            print(f"[{i:>2}] #{r['source_id']}  {fmt_score(float(r['distance']))}  {meta_str}")
            print(f"     {trunc(r['content'])}")
            print()

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
