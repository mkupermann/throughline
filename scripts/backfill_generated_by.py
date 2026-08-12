#!/usr/bin/env python3
"""Label already-stored conversations with whatever produced them.

The ingest-time filter stops new self-talk from being written. It could do
nothing about the years of it already in the database, and nothing at all about
the user's own scheduled tools, which are not Throughline's to discard.

Reads the first user message of every conversation, classifies it with
`throughline.self_referential.generated_by`, and writes the label. Never
deletes and never edits content — the only column it touches is
`generated_by`, which nothing else writes.

    python3 scripts/backfill_generated_by.py --dry-run   # counts only
    python3 scripts/backfill_generated_by.py             # apply

Safe to re-run: it recomputes every row from the text, so a widened marker list
is picked up on the next pass, and a row that no longer matches is cleared
rather than left stale.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from throughline.self_referential import (  # noqa: E402
    THROUGHLINE_GENERATORS,
    generated_by,
)
from throughline.status import _connect  # noqa: E402

#: Rows per UPDATE. Large enough to be quick, small enough that a cancelled run
#: leaves a partly-labelled table rather than a lock held over 80,000 rows.
_BATCH = 500


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Count, change nothing.")
    args = ap.parse_args()

    conn = _connect()
    if conn is None:
        print("Cannot reach PostgreSQL.", file=sys.stderr)
        return 2

    cur = conn.cursor()
    # LATERAL rather than a correlated subquery per row: one pass over the
    # messages index instead of one query per conversation.
    cur.execute(
        """
        SELECT c.id, c.generated_by, m.content
        FROM conversations c
        LEFT JOIN LATERAL (
            SELECT content FROM messages
            WHERE conversation_id = c.id AND role = 'user'
            ORDER BY id LIMIT 1
        ) m ON true
        """
    )
    rows = cur.fetchall()

    changes: list[tuple[str | None, int]] = []
    tally: collections.Counter = collections.Counter()
    for cid, current, first in rows:
        label = generated_by(first)
        tally[label or "(human)"] += 1
        if label != current:
            changes.append((label, cid))

    human = tally.get("(human)", 0)
    ours = sum(n for k, n in tally.items() if k in THROUGHLINE_GENERATORS)
    theirs = len(rows) - human - ours

    print(f"conversations:            {len(rows):,}")
    print(f"  written by a person:    {human:,}")
    print(f"  Throughline's own:      {ours:,}")
    print(f"  other automation:       {theirs:,}")
    print()
    for label, n in tally.most_common():
        print(f"  {n:6,}  {label}")
    print()
    print(f"rows whose label changes: {len(changes):,}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        conn.close()
        return 0

    if not changes:
        print("\nNothing to do.")
        conn.close()
        return 0

    for i in range(0, len(changes), _BATCH):
        batch = changes[i : i + _BATCH]
        cur.executemany("UPDATE conversations SET generated_by = %s WHERE id = %s", batch)
        conn.commit()
        print(f"  … {min(i + _BATCH, len(changes)):,}/{len(changes):,}", file=sys.stderr)

    print(f"\nLabelled {len(changes):,} conversations. Nothing was deleted.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
