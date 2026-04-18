#!/usr/bin/env python3
"""
Self-Reflecting Memory Engine
=============================

Pflegt die memory_chunks-DB durch 4 Reflection-Modi:

  - dedup            Paarweises Finden + Mergen von Near-Duplicates pro (category, project)
  - contradictions   Paarweises Finden widerspruechlicher decisions/patterns/preferences
  - stale            Datumsbezogene/zeitgebundene Chunks -> expires_at setzen, abgelaufene -> status=stale
  - consolidate      Cluster (>=3 thematisch verwandte Chunks) -> zusaetzlicher Super-Chunk

Nutzt den lokalen Claude CLI (Sonnet) fuer semantische Urteile. Keine API-Keys.
Jede Aktion wird in memory_reflections geloggt.

Aufruf:
  reflect_memory.py                     # alle 4 Modi
  reflect_memory.py --mode dedup
  reflect_memory.py --mode contradictions
  reflect_memory.py --mode stale
  reflect_memory.py --mode consolidate
  reflect_memory.py --dry-run           # nichts schreiben, nur reporten
  reflect_memory.py --limit 30          # max. N Chunks pro Modus (CLI-calls begrenzen)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras


# ---- Konfiguration ----------------------------------------------------------

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


def _resolve_claude_bin() -> str:
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    from shutil import which
    found = which("claude")
    return found or "claude"


CLAUDE_BIN = _resolve_claude_bin()
MODEL = "sonnet"
TIMEOUT_PER_CALL = 90
SLEEP_BETWEEN_CALLS = 1.0

# Maximum Anzahl Paar-Vergleiche pro Modus (schuetzt vor Quadratic Blow-up)
MAX_PAIRS_DEFAULT = 80

CONTRA_CATEGORIES = ("decision", "pattern", "preference")
STALE_TRIGGERS = [
    "heute", "aktuell", "derzeit", "momentan", "gerade", "jetzt",
    "diese woche", "naechste woche", "nächste woche", "meeting am",
    "termin am", "am ", "q1", "q2", "q3", "q4", "2025", "2026",
    "release", "sprint", "kw ",
]
DATE_PATTERN = re.compile(
    r"\b(\d{1,2}\.\d{1,2}\.(?:\d{2,4})?|\d{4}-\d{2}-\d{2}|"
    r"q[1-4]/\d{2,4}|kw\s?\d{1,2})\b",
    re.IGNORECASE,
)


# ---- Utility ----------------------------------------------------------------

def call_claude(prompt: str) -> str:
    """Ruft den lokalen Claude CLI auf (headless). Gibt stdout zurueck oder ''."""
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--model", MODEL],
            capture_output=True, text=True, timeout=TIMEOUT_PER_CALL,
        )
        if result.returncode != 0:
            print(f"    [claude] exit {result.returncode}: {result.stderr[:200]}")
            return ""
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"    [claude] timeout ({TIMEOUT_PER_CALL}s)")
        return ""
    except FileNotFoundError:
        print(f"    [claude] Binary nicht gefunden: {CLAUDE_BIN}")
        return ""
    except Exception as e:
        print(f"    [claude] exception: {e}")
        return ""


def parse_json_object(text: str) -> dict | None:
    """Findet das erste vernuenftige JSON-Objekt im Text."""
    if not text:
        return None
    text = text.strip()
    # strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _first_val(row):
    """Gibt das erste Feld eines fetchone() zurueck — funktioniert fuer tuple und RealDictCursor."""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def log_reflection(cur, reflection_type: str, affected: list[int],
                   action: str, reasoning: str, confidence: float) -> int:
    cur.execute(
        """
        INSERT INTO memory_reflections (reflection_type, affected_chunks,
                                        action_taken, reasoning, confidence)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (reflection_type, affected, action, reasoning[:4000], confidence),
    )
    return _first_val(cur.fetchone())


# ---- MODE: dedup ------------------------------------------------------------

DEDUP_PROMPT = """Du bekommst zwei Memory-Chunks aus einer persoenlichen Wissensdatenbank.

Chunk A (ID {id_a}, erstellt {date_a}):
{content_a}

Chunk B (ID {id_b}, erstellt {date_b}):
{content_b}

Beurteile: Sagen diese Chunks im Kern dasselbe (nahe Duplikate oder Umformulierungen),
oder sind sie komplementaer (ergaenzen sich, unterschiedlicher Kontext)?

Antworte REINES JSON ohne Markdown-Fences:
{{"duplicate": true|false, "confidence": 0.0-1.0, "reasoning": "kurz, 1-2 Saetze"}}
"""

MERGE_PROMPT = """Du bekommst zwei Memory-Chunks die denselben Sachverhalt beschreiben.
Formuliere einen einzigen konsolidierten Chunk der beide Inhalte verbindet,
kompakt und verlustarm. Behalte wichtige Details (Namen, Zahlen, Projekte).

Chunk A: {content_a}
Chunk B: {content_b}

Antworte mit REINEM JSON:
{{"content": "Merged content string"}}
"""


def mode_dedup(cur, conn, limit: int, max_pairs: int, dry_run: bool) -> dict:
    print("\n== MODE: dedup ==")
    cur.execute("""
        SELECT id, category::text AS category, project_name, content, created_at
        FROM memory_chunks
        WHERE status = 'active'
        ORDER BY id
    """)
    rows = cur.fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r["category"], r["project_name"] or "")
        groups.setdefault(key, []).append(r)

    pairs = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                pairs.append((items[i], items[j]))
    print(f"  Gruppen: {sum(1 for v in groups.values() if len(v) > 1)}  |  Paare: {len(pairs)}")

    if limit > 0 and len(pairs) > max_pairs:
        pairs = pairs[:max_pairs]
        print(f"  Begrenzt auf {max_pairs} Paare.")

    stats = {"pairs": len(pairs), "duplicates": 0, "merged": 0, "errors": 0}
    # Chunks die bereits gemergt wurden, sollen nicht erneut eingehen
    consumed: set[int] = set()

    for a, b in pairs:
        if a["id"] in consumed or b["id"] in consumed:
            continue
        prompt = DEDUP_PROMPT.format(
            id_a=a["id"], date_a=str(a["created_at"])[:10], content_a=a["content"],
            id_b=b["id"], date_b=str(b["created_at"])[:10], content_b=b["content"],
        )
        resp = call_claude(prompt)
        obj = parse_json_object(resp)
        if not obj or "duplicate" not in obj:
            print(f"  #{a['id']} vs #{b['id']}: kein Urteil")
            stats["errors"] += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue
        is_dup = bool(obj.get("duplicate"))
        conf = float(obj.get("confidence", 0.7))
        reason = str(obj.get("reasoning", ""))
        print(f"  #{a['id']} vs #{b['id']}: duplicate={is_dup} conf={conf:.2f}")

        if is_dup and conf >= 0.6:
            stats["duplicates"] += 1
            if dry_run:
                log_reflection(cur, "dedup", [a["id"], b["id"]],
                               "kept_both_dryrun", reason, conf)
                conn.commit()
                time.sleep(SLEEP_BETWEEN_CALLS)
                continue

            # Merge
            merge_prompt = MERGE_PROMPT.format(content_a=a["content"], content_b=b["content"])
            merge_resp = call_claude(merge_prompt)
            merge_obj = parse_json_object(merge_resp)
            merged_content = (merge_obj or {}).get("content") if merge_obj else None
            if not merged_content:
                # Fallback: simple concatenation
                merged_content = f"{a['content']}\n\n(Merge-Fallback) {b['content']}"

            # Tags/Projekt uebernehmen
            cur.execute("SELECT tags, confidence, project_name FROM memory_chunks WHERE id=%s", (a["id"],))
            a_meta = cur.fetchone()
            cur.execute("SELECT tags, confidence FROM memory_chunks WHERE id=%s", (b["id"],))
            b_meta = cur.fetchone()
            merged_tags = list({*(a_meta["tags"] or []), *(b_meta["tags"] or [])})
            merged_conf = max(float(a_meta["confidence"] or 0.8), float(b_meta["confidence"] or 0.8))

            cur.execute("""
                INSERT INTO memory_chunks
                    (source_type, source_id, content, category, tags, confidence,
                     project_name, merged_from, status)
                VALUES ('reflection_merge', NULL, %s, %s, %s, %s, %s, %s, 'active')
                RETURNING id
            """, (merged_content, a["category"], merged_tags, merged_conf,
                  a_meta["project_name"], [a["id"], b["id"]]))
            new_id = _first_val(cur.fetchone())

            now = datetime.now(timezone.utc)
            cur.execute("""
                UPDATE memory_chunks
                SET status='merged', superseded_by=%s, superseded_at=%s
                WHERE id IN (%s, %s)
            """, (new_id, now, a["id"], b["id"]))
            log_reflection(cur, "dedup", [a["id"], b["id"], new_id],
                           "merged", reason, conf)
            conn.commit()
            consumed.add(a["id"]); consumed.add(b["id"])
            stats["merged"] += 1
            print(f"    -> merged into #{new_id}")
        else:
            log_reflection(cur, "dedup", [a["id"], b["id"]],
                           "kept_both", reason, conf)
            conn.commit()

        time.sleep(SLEEP_BETWEEN_CALLS)

    return stats


# ---- MODE: contradictions ---------------------------------------------------

CONTRA_PROMPT = """Zwei Memory-Chunks aus einer persoenlichen Wissensdatenbank
({category}, Projekt {project}):

Chunk A (ID {id_a}, erstellt {date_a}):
{content_a}

Chunk B (ID {id_b}, erstellt {date_b}):
{content_b}

Widersprechen sich diese Aussagen sachlich (z.B. alte vs. neue Entscheidung,
unterschiedliche Praeferenz, gegensaetzliche Muster)? Oder sind sie einfach
verschieden/komplementaer?

Antworte REINES JSON:
{{"contradicts": true|false,
  "newer_id": <ID des neueren/gueltigen Chunks oder null>,
  "confidence": 0.0-1.0,
  "reasoning": "kurze Begruendung"}}
"""


def mode_contradictions(cur, conn, limit: int, max_pairs: int, dry_run: bool) -> dict:
    print("\n== MODE: contradictions ==")
    cur.execute(
        """
        SELECT id, category::text AS category, project_name, content, created_at
        FROM memory_chunks
        WHERE status = 'active' AND category::text = ANY(%s)
        ORDER BY id
        """,
        (list(CONTRA_CATEGORIES),),
    )
    rows = cur.fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r["category"], r["project_name"] or "")
        groups.setdefault(key, []).append(r)

    pairs = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                pairs.append((items[i], items[j]))
    print(f"  Paare zu pruefen: {len(pairs)}")

    if limit > 0 and len(pairs) > max_pairs:
        pairs = pairs[:max_pairs]
        print(f"  Begrenzt auf {max_pairs} Paare.")

    stats = {"pairs": len(pairs), "contradictions": 0, "superseded": 0, "errors": 0}
    for a, b in pairs:
        prompt = CONTRA_PROMPT.format(
            category=a["category"], project=a["project_name"] or "–",
            id_a=a["id"], date_a=str(a["created_at"])[:10], content_a=a["content"],
            id_b=b["id"], date_b=str(b["created_at"])[:10], content_b=b["content"],
        )
        resp = call_claude(prompt)
        obj = parse_json_object(resp)
        if not obj or "contradicts" not in obj:
            stats["errors"] += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        contradicts = bool(obj.get("contradicts"))
        conf = float(obj.get("confidence", 0.7))
        reason = str(obj.get("reasoning", ""))
        newer = obj.get("newer_id")
        print(f"  #{a['id']} vs #{b['id']}: contradicts={contradicts} conf={conf:.2f} newer={newer}")

        if contradicts and conf >= 0.6:
            stats["contradictions"] += 1
            # Automatisch: wenn newer_id gesetzt und ein Zeitunterschied vorliegt, supersede
            if newer in (a["id"], b["id"]):
                new_id, old_id = (a["id"], b["id"]) if newer == a["id"] else (b["id"], a["id"])
            else:
                # Fallback: der juengere Chunk gewinnt
                if a["created_at"] >= b["created_at"]:
                    new_id, old_id = a["id"], b["id"]
                else:
                    new_id, old_id = b["id"], a["id"]

            if not dry_run:
                now = datetime.now(timezone.utc)
                cur.execute("""
                    UPDATE memory_chunks
                    SET status='superseded', superseded_by=%s, superseded_at=%s
                    WHERE id=%s AND status='active'
                """, (new_id, now, old_id))
                stats["superseded"] += cur.rowcount
                log_reflection(cur, "contradiction", [a["id"], b["id"]],
                               f"superseded_{old_id}_by_{new_id}", reason, conf)
                conn.commit()
            else:
                log_reflection(cur, "contradiction", [a["id"], b["id"]],
                               "dryrun_would_supersede", reason, conf)
                conn.commit()
        else:
            log_reflection(cur, "contradiction", [a["id"], b["id"]],
                           "kept_both", reason, conf)
            conn.commit()

        time.sleep(SLEEP_BETWEEN_CALLS)

    return stats


# ---- MODE: stale ------------------------------------------------------------

STALE_PROMPT = """Ein Memory-Chunk aus einer persoenlichen Wissensdatenbank:

"{content}"
(Erstellt am {created_at}, heute ist {today})

Ist diese Information zeitlich begrenzt (verfallsdatiert)?
Beispiele fuer zeitlich begrenzt: konkrete Meetings, Sprint-Ziele, aktuelle Zustaende.
Beispiele fuer nicht zeitlich begrenzt: Architekturentscheidungen, Personen, dauerhafte Praeferenzen.

Falls zeitlich begrenzt: wann laeuft die Info voraussichtlich ab?

Antworte REINES JSON:
{{"stale": true|false,
  "expires_at": "YYYY-MM-DD" oder null,
  "confidence": 0.0-1.0,
  "reasoning": "kurz"}}
"""


def mode_stale(cur, conn, limit: int, dry_run: bool) -> dict:
    print("\n== MODE: stale ==")
    # Kandidaten: Content enthaelt Zeitbezug
    cur.execute("""
        SELECT id, content, created_at, expires_at
        FROM memory_chunks
        WHERE status = 'active'
        ORDER BY id
    """)
    rows = cur.fetchall()

    candidates = []
    for r in rows:
        content_lower = r["content"].lower()
        if DATE_PATTERN.search(content_lower) or any(t in content_lower for t in STALE_TRIGGERS):
            candidates.append(r)
    print(f"  Kandidaten (Zeitbezug): {len(candidates)}")

    if limit > 0 and len(candidates) > limit:
        candidates = candidates[:limit]
        print(f"  Begrenzt auf {limit} Kandidaten.")

    today = datetime.now(timezone.utc).date()
    stats = {"candidates": len(candidates), "stale_flagged": 0, "expires_set": 0, "errors": 0}

    for r in candidates:
        prompt = STALE_PROMPT.format(
            content=r["content"], created_at=str(r["created_at"])[:10], today=today.isoformat(),
        )
        resp = call_claude(prompt)
        obj = parse_json_object(resp)
        if not obj or "stale" not in obj:
            stats["errors"] += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        is_stale = bool(obj.get("stale"))
        expires_str = obj.get("expires_at")
        conf = float(obj.get("confidence", 0.7))
        reason = str(obj.get("reasoning", ""))
        print(f"  #{r['id']}: stale={is_stale} expires={expires_str} conf={conf:.2f}")

        if not is_stale:
            log_reflection(cur, "stale_detection", [r["id"]], "not_stale", reason, conf)
            conn.commit()
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        expires_date = None
        if expires_str:
            try:
                expires_date = datetime.fromisoformat(expires_str).replace(tzinfo=timezone.utc)
            except Exception:
                expires_date = None

        action = "kept_active"
        if not dry_run:
            if expires_date:
                cur.execute("UPDATE memory_chunks SET expires_at=%s WHERE id=%s",
                            (expires_date, r["id"]))
                stats["expires_set"] += 1
                action = "expires_set"
            if expires_date and expires_date.date() < today:
                cur.execute("UPDATE memory_chunks SET status='stale' WHERE id=%s AND status='active'",
                            (r["id"],))
                stats["stale_flagged"] += cur.rowcount
                action = "marked_stale"
        log_reflection(cur, "stale_detection", [r["id"]], action, reason, conf)
        conn.commit()
        time.sleep(SLEEP_BETWEEN_CALLS)

    return stats


# ---- MODE: consolidate ------------------------------------------------------

CONSOLIDATE_PROMPT = """Du bekommst mehrere Memory-Chunks zum gleichen Thema.
Erstelle einen uebergeordneten Super-Chunk, der die Kernaussagen aller
Chunks in kondensierter Form zusammenfasst. Der Super-Chunk soll als
stabile Referenz dienen (sichtbar verwandt, aber nicht redundant zu Original).

Chunks:
{items}

Antworte REINES JSON:
{{"content": "Konsolidierter Inhalt",
  "tags": ["tag1", "tag2"],
  "reasoning": "kurze Begruendung warum zusammengefasst"}}
"""


def mode_consolidate(cur, conn, limit: int, dry_run: bool) -> dict:
    print("\n== MODE: consolidate ==")
    # Cluster-Heuristik: fuer jedes Tag -> Liste aller Chunks mit diesem Tag,
    # gefiltert nach gleicher category + gleichem Projekt. Ab 3 Chunks = Cluster.
    cur.execute("""
        SELECT id, category::text AS category, project_name, content, tags, confidence
        FROM memory_chunks
        WHERE status = 'active'
    """)
    rows = cur.fetchall()

    # Pro (category, project, tag) ein Bucket
    buckets: dict[tuple, dict[int, Any]] = {}
    for r in rows:
        tags = r["tags"] or []
        if not tags:
            continue
        for t in tags:
            key = (r["category"], r["project_name"] or "", t.lower())
            buckets.setdefault(key, {})[r["id"]] = r

    # Deduplikation der Cluster (ein Chunk kann via mehrere Tags rein — wir
    # behalten den Bucket mit den meisten Chunks, groesster gewinnt)
    all_clusters = [(k, list(v.values())) for k, v in buckets.items() if len(v) >= 3]
    # Sortiere nach Clustergroesse, groesster zuerst
    all_clusters.sort(key=lambda x: -len(x[1]))

    # Vermeide Ueberschneidung: wenn ein Chunk schon in einem groesseren Cluster ist,
    # nicht erneut konsolidieren.
    clusters = []
    seen_ids: set[int] = set()
    for k, items in all_clusters:
        fresh = [i for i in items if i["id"] not in seen_ids]
        if len(fresh) >= 3:
            clusters.append((k, fresh))
            for i in fresh:
                seen_ids.add(i["id"])

    print(f"  Kandidaten-Cluster (>=3 Chunks, gleicher Tag/cat/project): {len(clusters)}")

    if limit > 0 and len(clusters) > limit:
        clusters = clusters[:limit]

    stats = {"clusters": len(clusters), "consolidated": 0, "errors": 0}
    for key, items in clusters:
        ids = [i["id"] for i in items]
        items_text = "\n\n".join(
            f"[ID {i['id']}] {i['content']}" for i in items[:8]  # cap per cluster
        )
        prompt = CONSOLIDATE_PROMPT.format(items=items_text)
        resp = call_claude(prompt)
        obj = parse_json_object(resp)
        if not obj or "content" not in obj:
            stats["errors"] += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        content = str(obj.get("content", "")).strip()
        # key Format: (category, project_or_empty, tag)
        cat_k, proj_k, tag_k = key
        tags = obj.get("tags") or [tag_k]
        reason = str(obj.get("reasoning", ""))
        if not content or len(content) < 40:
            stats["errors"] += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue
        print(f"  Cluster {cat_k}/{tag_k}/{proj_k}: {len(items)} Chunks -> Super-Chunk")

        if dry_run:
            log_reflection(cur, "consolidation", ids, "dryrun", reason, 0.9)
            conn.commit()
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        cur.execute("""
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, tags, confidence,
                 project_name, merged_from, status)
            VALUES ('consolidation', NULL, %s, %s, %s, %s, %s, %s, 'active')
            RETURNING id
        """, (content, items[0]["category"], list(tags), 0.9,
              items[0]["project_name"], ids))
        new_id = _first_val(cur.fetchone())
        log_reflection(cur, "consolidation", ids + [new_id], "created_super_chunk", reason, 0.9)
        conn.commit()
        stats["consolidated"] += 1
        time.sleep(SLEEP_BETWEEN_CALLS)

    return stats


# ---- Main -------------------------------------------------------------------

def print_report(stats_by_mode: dict[str, dict[str, Any]]) -> None:
    print("\n" + "=" * 60)
    print("REFLECTION REPORT")
    print("=" * 60)
    for mode, s in stats_by_mode.items():
        print(f"\n[{mode}]")
        for k, v in s.items():
            print(f"  {k:20} {v}")


def main() -> None:
    p = argparse.ArgumentParser(description="Self-Reflecting Memory Engine")
    p.add_argument("--mode", choices=["dedup", "contradictions", "stale", "consolidate"],
                   help="Einzelnen Modus ausfuehren (default: alle)")
    p.add_argument("--limit", type=int, default=MAX_PAIRS_DEFAULT,
                   help="Max. Paare/Kandidaten pro Modus (default 80)")
    p.add_argument("--max-pairs", type=int, default=MAX_PAIRS_DEFAULT,
                   help="Alias fuer --limit fuer paar-basierte Modi")
    p.add_argument("--dry-run", action="store_true",
                   help="Keine Writes, nur Analyse + Log")
    args = p.parse_args()

    _require_claude_bin()
    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("Self-Reflecting Memory Engine")
    print(f"  DB: {DB_CONFIG['dbname']}@{DB_CONFIG['host']}")
    print(f"  Model: {MODEL}  |  dry_run={args.dry_run}  |  limit={args.limit}")

    modes = [args.mode] if args.mode else ["dedup", "contradictions", "stale", "consolidate"]
    stats_by_mode: dict[str, dict] = {}
    for m in modes:
        if m == "dedup":
            stats_by_mode[m] = mode_dedup(cur, conn, args.limit, args.max_pairs, args.dry_run)
        elif m == "contradictions":
            stats_by_mode[m] = mode_contradictions(cur, conn, args.limit, args.max_pairs, args.dry_run)
        elif m == "stale":
            stats_by_mode[m] = mode_stale(cur, conn, args.limit, args.dry_run)
        elif m == "consolidate":
            stats_by_mode[m] = mode_consolidate(cur, conn, args.limit, args.dry_run)

    print_report(stats_by_mode)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
