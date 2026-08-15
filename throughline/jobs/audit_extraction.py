#!/usr/bin/env python3
"""Memory-extraction drift audit.

Sample N random memory chunks, fetch their source conversation messages,
and check whether the chunk's distinctive words still appear in the
source. A chunk that no longer overlaps with its source is *drifted* —
the LLM summariser either hallucinated content or the source has
diverged from what the chunk claims.

Why a heuristic and not an LLM judge?
  - Free + deterministic + fast enough to run on cron.
  - Catches the worst class of drift (chunks with no overlap at all),
    which is exactly the failure mode the Hermes review highlighted:
    multi-axis plans collapsed into generic project_context blurbs
    that *do* mention the project but lose every specific term.
  - A future v2 can add an embedding-cosine mode behind --semantic, or
    delegate spot-checks to Claude behind --llm — the audit-row schema
    in memory_reflections already accommodates both.

Each run writes exactly one row to ``memory_reflections`` with
``reflection_type='audit'``, listing the chunk IDs that were sampled
and flagging the ones that fell below the drift threshold.

Usage:
  audit_extraction.py                   # sample 20, write audit row
  audit_extraction.py --limit 50        # sample 50
  audit_extraction.py --threshold 0.40  # stricter recall floor
  audit_extraction.py --dry-run         # compute + print, do not write
  audit_extraction.py --json            # JSON output for cron/monitor
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import psycopg2
import psycopg2.extras


DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "throughline"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

DEFAULT_SAMPLE_SIZE = 20
DEFAULT_DRIFT_THRESHOLD = 0.30
MAX_SOURCE_CHARS = 200_000  # cap per-chunk source text we hold in memory


# Small stoplist — keeping the wordset tight so domain-specific terms
# (function names, paths, tool names) dominate the recall signal.
_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "have", "has", "had", "was", "were", "are", "been", "being",
    "into", "onto", "than", "then", "them", "they", "them",
    "but", "not", "but", "yes", "you", "your", "yours", "our", "ours",
    "would", "could", "should", "will", "shall", "can", "may", "might",
    "der", "die", "das", "und", "oder", "nicht", "ist", "sind", "wird",
    "kann", "muss", "soll", "darf", "also", "noch", "nur", "auch",
    "ein", "eine", "einen", "eines", "einer", "einem",
    "mit", "ohne", "ueber", "über", "unter", "auf", "aus", "vor",
}


def _connect() -> "psycopg2.extensions.connection":
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}.\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e


def meaningful_words(text: str) -> set[str]:
    """Return the set of distinctive ≥4-character word tokens in *text*,
    lower-cased and stop-filtered. Numbers are kept; pure punctuation is
    dropped. This is the basis of the recall metric.
    """
    if not text:
        return set()
    tokens = re.findall(r"\b\w{4,}\b", text.lower())
    return {t for t in tokens if t not in _STOP}


@dataclass
class ChunkAudit:
    """Audit result for a single chunk."""

    chunk_id: int
    source_type: str
    source_id: int | None
    category: str
    recall: float
    chunk_word_count: int
    source_word_count: int
    drifted: bool
    reason: str = ""
    content_preview: str = field(default="", repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "category": self.category,
            "recall": round(self.recall, 4),
            "chunk_word_count": self.chunk_word_count,
            "source_word_count": self.source_word_count,
            "drifted": self.drifted,
            "reason": self.reason,
            "content_preview": self.content_preview,
        }


def audit_chunk(
    chunk_content: str,
    source_text: str,
    *,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> tuple[float, bool, str]:
    """Compute token-recall of *chunk_content* against *source_text*.

    Returns ``(recall, drifted, reason)``:
      - ``recall``  : fraction of the chunk's distinctive words found in
                      the source; 1.0 means every word appears.
      - ``drifted`` : True iff ``recall < threshold``.
      - ``reason``  : short tag for downstream filtering. One of
                      ``""``, ``"no_source"``, ``"vacuous"``, ``"low_recall"``.

    A chunk with no meaningful words (very short, or only stop words)
    is treated as vacuous — recall is 1.0 by convention and the chunk
    is NOT flagged. A chunk whose source could not be located gets
    ``no_source`` and is flagged.
    """
    chunk_words = meaningful_words(chunk_content)
    if not chunk_words:
        return 1.0, False, "vacuous"
    if not source_text:
        return 0.0, True, "no_source"
    source_words = meaningful_words(source_text)
    overlap = chunk_words & source_words
    recall = len(overlap) / len(chunk_words)
    drifted = recall < threshold
    return recall, drifted, ("low_recall" if drifted else "")


def _fetch_source_text(cur, source_type: str, source_id: int | None) -> str:
    """Return the concatenated message bodies for the chunk's source.

    Only ``source_type='conversation'`` is supported today — that's the
    only path the extractor writes.
    """
    if source_id is None or source_type != "conversation":
        return ""
    cur.execute(
        "SELECT content FROM messages "
        "WHERE conversation_id = %s AND role IN ('user', 'assistant') "
        "ORDER BY created_at",
        (source_id,),
    )
    parts: list[str] = []
    total = 0
    for (content,) in cur.fetchall():
        if not content:
            continue
        parts.append(content)
        total += len(content)
        if total > MAX_SOURCE_CHARS:
            break
    return "\n".join(parts)


def sample_chunks(cur, *, limit: int) -> list[dict[str, Any]]:
    """Return ``limit`` random active chunks attributable to a conversation."""
    cur.execute(
        """
        SELECT id, source_type, source_id, category::text AS category,
               content, project_name
        FROM memory_chunks
        WHERE COALESCE(status, 'active') = 'active'
          AND source_type = 'conversation'
          AND source_id IS NOT NULL
        ORDER BY random()
        LIMIT %s
        """,
        (limit,),
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def run_audit(
    conn,
    *,
    limit: int = DEFAULT_SAMPLE_SIZE,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
    write_audit_row: bool = True,
) -> dict[str, Any]:
    """Run one audit pass. Returns the JSON summary that ``--json`` prints
    and that ``memory_reflections`` records."""
    with conn.cursor() as cur:
        rows = sample_chunks(cur, limit=limit)
        results: list[ChunkAudit] = []
        for row in rows:
            source_text = _fetch_source_text(cur, row["source_type"], row["source_id"])
            recall, drifted, reason = audit_chunk(
                row["content"] or "", source_text, threshold=threshold,
            )
            results.append(ChunkAudit(
                chunk_id=int(row["id"]),
                source_type=row["source_type"],
                source_id=int(row["source_id"]) if row["source_id"] is not None else None,
                category=row["category"],
                recall=recall,
                chunk_word_count=len(meaningful_words(row["content"] or "")),
                source_word_count=len(meaningful_words(source_text)),
                drifted=drifted,
                reason=reason,
                content_preview=(row["content"] or "")[:140],
            ))

        drifted_ids = [r.chunk_id for r in results if r.drifted]
        summary = {
            "sampled": len(results),
            "drifted": len(drifted_ids),
            "drift_rate": round(len(drifted_ids) / len(results), 4) if results else 0.0,
            "threshold": threshold,
            "mean_recall": round(
                sum(r.recall for r in results) / len(results), 4
            ) if results else 1.0,
            "drifted_ids": drifted_ids,
            "by_category": _by_category(results),
            "examples": [r.to_dict() for r in results if r.drifted][:5],
        }

        if write_audit_row and results:
            sampled_ids = [r.chunk_id for r in results]
            reasoning = (
                f"Sampled {len(results)} chunks, "
                f"mean recall {summary['mean_recall']:.2f}, "
                f"threshold {threshold:.2f}, "
                f"{len(drifted_ids)} drifted."
            )
            cur.execute(
                """
                INSERT INTO memory_reflections
                    (reflection_type, affected_chunks, action_taken, reasoning, confidence)
                VALUES ('audit', %s, %s, %s, 1.0)
                RETURNING id
                """,
                (
                    sampled_ids,
                    "flagged_drift" if drifted_ids else "no_drift_detected",
                    reasoning[:4000],
                ),
            )
            row = cur.fetchone()
            summary["reflection_id"] = int(row[0]) if row else None
            conn.commit()
        else:
            summary["reflection_id"] = None
            conn.rollback()
    return summary


def _by_category(results: list[ChunkAudit]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = out.setdefault(r.category, {"sampled": 0, "drifted": 0})
        bucket["sampled"] += 1
        if r.drifted:
            bucket["drifted"] += 1
    return out


def _print_human(summary: dict[str, Any]) -> None:
    print("=" * 60)
    print(f"Memory-extraction drift audit")
    print("=" * 60)
    print(f"  Sampled       : {summary['sampled']}")
    print(f"  Drifted       : {summary['drifted']}  "
          f"(rate {summary['drift_rate']*100:.1f}%)")
    print(f"  Mean recall   : {summary['mean_recall']:.2f}")
    print(f"  Threshold     : {summary['threshold']:.2f}")
    if summary["by_category"]:
        print("  By category   :")
        for cat, counts in sorted(summary["by_category"].items()):
            print(f"    {cat:<18s} {counts['drifted']:>3d} / {counts['sampled']:>3d}")
    if summary["examples"]:
        print("  Drifted examples (top 5):")
        for ex in summary["examples"]:
            print(f"    #{ex['chunk_id']:<5d} [{ex['category']:<14s}] "
                  f"recall {ex['recall']:.2f} | {ex['content_preview']}")
    if summary.get("reflection_id"):
        print(f"  Audit row     : memory_reflections #{summary['reflection_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sample memory chunks and flag those that have drifted "
                    "from their source conversation."
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_SAMPLE_SIZE,
                        help=f"Number of chunks to sample (default {DEFAULT_SAMPLE_SIZE}).")
    parser.add_argument("--threshold", type=float, default=DEFAULT_DRIFT_THRESHOLD,
                        help=f"Recall floor below which a chunk is flagged as drift "
                             f"(default {DEFAULT_DRIFT_THRESHOLD}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute + report, do NOT write the audit row.")
    parser.add_argument("--json", action="store_true",
                        help="Emit a single JSON document for cron / monitoring scrapes.")
    args = parser.parse_args()

    conn = _connect()
    try:
        summary = run_audit(
            conn,
            limit=args.limit,
            threshold=args.threshold,
            write_audit_row=not args.dry_run,
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_human(summary)


if __name__ == "__main__":
    main()
