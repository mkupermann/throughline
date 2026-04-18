#!/usr/bin/env python3
"""
Applies pending SQL migrations from ``sql/migrations/``.

The runner is deliberately minimal:

- Ensures the ``applied_migrations`` tracking table exists.
- Lists every ``NNN_*.sql`` file in ``sql/migrations/`` in lexicographic order.
- Runs any file whose name is not yet recorded in ``applied_migrations``, then
  records it.

Usage::

    python3 scripts/migrate.py               # apply pending
    python3 scripts/migrate.py --status      # show applied vs. pending
    python3 scripts/migrate.py --dry-run     # print the plan, do not execute

Environment (standard libpq vars, same as the rest of the project):

    PGDATABASE, PGUSER, PGHOST, PGPORT, PGPASSWORD
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "sql" / "migrations"

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS applied_migrations (
    migration_name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
);
"""


def db_config() -> dict:
    return {
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return every ``NNN_*.sql`` file in lexicographic order."""
    if not migrations_dir.is_dir():
        return []
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def applied_set(cursor) -> set[str]:
    cursor.execute("SELECT migration_name FROM applied_migrations")
    return {row[0] for row in cursor.fetchall()}


def run_migration(cursor, migration: Path) -> None:
    sql = migration.read_text(encoding="utf-8")
    cursor.execute(sql)
    cursor.execute(
        "INSERT INTO applied_migrations (migration_name) VALUES (%s) "
        "ON CONFLICT DO NOTHING",
        (migration.name,),
    )


def cmd_status(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(TRACKING_DDL)
        conn.commit()
        done = applied_set(cur)
    all_migrations = discover_migrations()
    if not all_migrations:
        print(f"No migrations found in {MIGRATIONS_DIR}")
        return 0
    print(f"{'STATUS':<9} MIGRATION")
    print("-" * 60)
    for m in all_migrations:
        tag = "applied" if m.name in done else "pending"
        print(f"{tag:<9} {m.name}")
    pending = sum(1 for m in all_migrations if m.name not in done)
    print("-" * 60)
    print(f"{len(all_migrations)} total, {pending} pending")
    return 0


def cmd_migrate(conn, dry_run: bool) -> int:
    with conn.cursor() as cur:
        cur.execute(TRACKING_DDL)
        conn.commit()
        done = applied_set(cur)

    pending = [m for m in discover_migrations() if m.name not in done]
    if not pending:
        print("Nothing to do. Database is up to date.")
        return 0

    print(f"{len(pending)} pending migration(s):")
    for m in pending:
        print(f"  - {m.name}")

    if dry_run:
        print("\n--dry-run set; exiting without applying.")
        return 0

    for m in pending:
        print(f"\n==> Applying {m.name}")
        try:
            with conn.cursor() as cur:
                run_migration(cur, m)
            conn.commit()
            print(f"    OK ({m.name})")
        except Exception as exc:
            conn.rollback()
            print(f"    FAIL ({m.name}): {exc}", file=sys.stderr)
            return 1

    print("\nAll migrations applied.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="Show applied vs. pending and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without applying.")
    args = parser.parse_args(argv)

    try:
        conn = psycopg2.connect(**db_config())
    except psycopg2.Error as exc:
        print(f"Could not connect to database: {exc}", file=sys.stderr)
        return 2

    try:
        if args.status:
            return cmd_status(conn)
        return cmd_migrate(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
