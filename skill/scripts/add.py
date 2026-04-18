#!/usr/bin/env python3
"""Claude Memory — Add a memory chunk to the DB from the command line.

Used by the `claude-memory` skill to persist new decisions, patterns, or
insights discovered during a Claude Code session.

Example:
    python3 add.py \\
        --category decision \\
        --content "We use pgvector instead of Qdrant — one fewer service to run." \\
        --project claude-memory \\
        --tags postgresql,pgvector,architecture \\
        --confidence 0.9
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

VALID_CATEGORIES = (
    "decision",
    "pattern",
    "insight",
    "preference",
    "contact",
    "error_solution",
    "project_context",
    "workflow",
)

DB = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Add a memory chunk to the claude_memory DB.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--category", required=True, choices=VALID_CATEGORIES,
                   help="Category of the memory chunk.")
    p.add_argument("--content", required=True,
                   help="The insight itself (one clear sentence is best).")
    p.add_argument("--project", default=None,
                   help="Optional project name to scope the chunk.")
    p.add_argument("--tags", default="",
                   help="Comma-separated tags (e.g. 'postgresql,pgvector').")
    p.add_argument("--confidence", type=float, default=0.8,
                   help="Confidence in [0.0, 1.0].")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not (0.0 <= args.confidence <= 1.0):
        print("ERROR: --confidence must be between 0.0 and 1.0", file=sys.stderr)
        return 2

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    try:
        conn = psycopg2.connect(**DB)
    except Exception as e:
        print(f"ERROR: could not connect to DB: {e}", file=sys.stderr)
        return 1

    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_chunks
                    (source_type, source_id, content, category, tags,
                     confidence, project_name)
                VALUES ('manual', NULL, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (args.content, args.category, tags, args.confidence, args.project),
            )
            row = cur.fetchone()
            new_id = row[0] if row else None
            print(f"OK — inserted memory_chunks.id = {new_id}")
        return 0
    except Exception as e:
        print(f"ERROR: insert failed: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
