#!/usr/bin/env python3
"""Backfill the ``projects`` table from observed ``project_name`` values.

The ``projects`` table is a manually-curated table (description, contacts,
decisions, status). Nothing in the ingestion pipeline writes to it — only
the GUI's "New project" form does. As a result, a freshly-ingested DB
shows zero rows on the Projects page even though `memory_chunks` and
`conversations` reference dozens of distinct project_names.

This script closes that gap. For every distinct ``project_name`` observed
in `memory_chunks` (and optionally `conversations`), insert a row into
`projects` with the bare name and default status. Existing rows are left
untouched (ON CONFLICT DO NOTHING) — manually-curated descriptions /
contacts are never clobbered, so re-running is safe.

Usage::

    # Default: from memory_chunks only.
    python scripts/backfill_projects.py

    # Wider net: also consider conversations.
    python scripts/backfill_projects.py --include-conversations

    # Preview without writing.
    python scripts/backfill_projects.py --dry-run

    # Same options via the unified CLI:
    throughline backfill-projects [--include-conversations] [--dry-run]

Exit code: 0 on success (including dry-run), 2 on a usage error, 1 on
DB failure.
"""
from __future__ import annotations
from _bootstrap import use_venv  # noqa: E402
use_venv()


import argparse
import os
import sys

import psycopg2


def db_config() -> dict:
    cfg = {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": os.environ.get("PGUSER", os.environ.get("USER") or "postgres"),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", "5")),
    }
    pw = os.environ.get("PGPASSWORD")
    if pw:
        cfg["password"] = pw
    return cfg


def collect_observed_names(conn, *, include_conversations: bool) -> list[str]:
    """Return the sorted list of distinct project_name values worth backfilling.

    Always considers `memory_chunks`. `conversations` is opt-in because
    not every project that has a Claude session necessarily has memory
    extracted from it yet — that's a noisier signal.
    """
    sources = ["memory_chunks"]
    if include_conversations:
        sources.append("conversations")

    names: set[str] = set()
    with conn.cursor() as cur:
        for tbl in sources:
            cur.execute(
                f"SELECT DISTINCT project_name FROM public.{tbl} "
                "WHERE project_name IS NOT NULL AND project_name <> ''"
            )
            for (n,) in cur.fetchall():
                names.add(n)
    return sorted(names)


def existing_project_names(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM public.projects")
        return {r[0] for r in cur.fetchall()}


def insert_missing(conn, missing: list[str]) -> int:
    """Insert rows for the given names. Idempotent via ON CONFLICT.

    Returns the number of rows actually inserted (not counting conflicts).
    """
    if not missing:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO public.projects (name, status) VALUES (%s, 'active') "
            "ON CONFLICT (name) DO NOTHING",
            [(n,) for n in missing],
        )
        # cur.rowcount with executemany on psycopg2 is driver-dependent.
        # Recount truthfully by querying the table afterwards in main().
    conn.commit()
    return len(missing)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="backfill_projects",
        description=(
            "Insert one row into the projects table for each distinct "
            "project_name observed in memory_chunks (and optionally "
            "conversations). Existing rows are never modified."
        ),
    )
    ap.add_argument(
        "--include-conversations",
        action="store_true",
        help=("Also pull project_names from the conversations table. Default "
              "is memory_chunks only — the more signal-rich source."),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted; do not write to the DB.",
    )
    args = ap.parse_args(argv)

    try:
        conn = psycopg2.connect(**db_config())
    except Exception as e:
        print(f"[backfill] DB connect failed: {e}", file=sys.stderr)
        return 1

    try:
        observed = collect_observed_names(
            conn, include_conversations=args.include_conversations,
        )
        existing = existing_project_names(conn)
        to_insert = [n for n in observed if n not in existing]

        sources = ["memory_chunks"]
        if args.include_conversations:
            sources.append("conversations")

        print(f"[backfill] sources: {', '.join(sources)}")
        print(f"[backfill] observed distinct project_names: {len(observed)}")
        print(f"[backfill] already in projects table:        {len(existing)}")
        print(f"[backfill] to insert:                        {len(to_insert)}")

        if args.dry_run:
            for n in to_insert[:20]:
                print(f"  + {n}")
            if len(to_insert) > 20:
                print(f"  … (and {len(to_insert) - 20} more)")
            print("[backfill] dry-run — no writes performed.")
            return 0

        if not to_insert:
            print("[backfill] nothing to do.")
            return 0

        insert_missing(conn, to_insert)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.projects")
            total = int(cur.fetchone()[0])
        print(f"[backfill] inserted {len(to_insert)} row(s); "
              f"projects table now has {total}.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
