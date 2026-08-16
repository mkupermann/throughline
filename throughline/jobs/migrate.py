#!/usr/bin/env python3
"""
Applies pending SQL migrations from the packaged ``throughline.migrations``.

The runner is deliberately minimal:

- Ensures the ``applied_migrations`` tracking table exists.
- Validates and lists every ``NNN_*.sql`` file in ordinal order.
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
import re
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_NAME = re.compile(r"(?P<ordinal>\d{3})_[a-z0-9_]+\.sql\Z")

# 001_widen_conversation_token_counts.sql shipped with the same ordinal as
# 001_message_dedup.sql. Existing databases may have recorded that filename.
# Its replacement is therefore treated as applied when the historical name is
# present; do not rewrite tracking rows, because those are deployment history.
LEGACY_MIGRATION_NAMES: dict[str, frozenset[str]] = {
    "005_widen_conversation_token_counts.sql": frozenset(
        {"001_widen_conversation_token_counts.sql"}
    ),
}

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS public.applied_migrations (
    migration_name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
);
"""


class MigrationValidationError(ValueError):
    """Raised when migration filenames cannot define one unambiguous order."""


def db_config() -> dict:
    return {
        "dbname": os.environ.get("PGDATABASE", "throughline"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    """Return valid migrations in ordinal order, rejecting ambiguous filenames."""
    if not migrations_dir.is_dir():
        return []
    migrations = sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())
    invalid = [migration.name for migration in migrations if not MIGRATION_NAME.fullmatch(migration.name)]
    if invalid:
        raise MigrationValidationError(
            "migration filenames must match NNN_lowercase_description.sql: " + ", ".join(invalid)
        )

    ordinals: dict[str, list[str]] = {}
    for migration in migrations:
        ordinal = MIGRATION_NAME.fullmatch(migration.name).group("ordinal")  # type: ignore[union-attr]
        ordinals.setdefault(ordinal, []).append(migration.name)
    duplicates = {ordinal: names for ordinal, names in ordinals.items() if len(names) > 1}
    if duplicates:
        ordinal, names = next(iter(duplicates.items()))
        raise MigrationValidationError(f"duplicate migration ordinal {ordinal}: {', '.join(names)}")
    return sorted(migrations, key=lambda migration: (migration.name[:3], migration.name))


def applied_set(cursor) -> set[str]:
    cursor.execute("SELECT migration_name FROM public.applied_migrations")
    return {row[0] for row in cursor.fetchall()}


def is_applied(migration: Path, applied: set[str]) -> bool:
    """Whether a current migration or one of its preserved historic names ran."""
    return migration.name in applied or bool(LEGACY_MIGRATION_NAMES.get(migration.name, set()) & applied)


def _executable_sql(sql: str) -> str:
    """Strip pg_dump's psql-only guard commands before sending SQL to libpq."""
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("\\"))


def _has_existing_schema(cursor) -> bool:
    """Recognise the previous Compose ``schema.sql`` initialization path."""
    cursor.execute("SELECT to_regclass('public.conversations') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])


def _bootstrap_existing_schema(cursor, applied: set[str], migrations: list[Path]) -> set[str]:
    """Record only the baseline for a schema initialized before migrations ran."""
    if not migrations:
        return applied
    baseline = migrations[0]
    if baseline.name in applied or not _has_existing_schema(cursor):
        return applied
    cursor.execute(
        "INSERT INTO public.applied_migrations (migration_name) VALUES (%s) ON CONFLICT DO NOTHING",
        (baseline.name,),
    )
    print(f"Existing Throughline schema detected; recording {baseline.name} as the baseline.")
    return applied | {baseline.name}


def run_migration(cursor, migration: Path, migration_name: str | None = None) -> None:
    cursor.execute("SET LOCAL search_path TO public")
    sql = _executable_sql(migration.read_text(encoding="utf-8"))
    cursor.execute(sql)
    cursor.execute(
        "INSERT INTO public.applied_migrations (migration_name) VALUES (%s) "
        "ON CONFLICT DO NOTHING",
        (migration_name or migration.name,),
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
        tag = "applied" if is_applied(m, done) else "pending"
        print(f"{tag:<9} {m.name}")
    pending = sum(1 for m in all_migrations if not is_applied(m, done))
    print("-" * 60)
    print(f"{len(all_migrations)} total, {pending} pending")
    return 0


def cmd_migrate(conn, dry_run: bool) -> int:
    all_migrations = discover_migrations()
    with conn.cursor() as cur:
        cur.execute(TRACKING_DDL)
        done = applied_set(cur)
        done = _bootstrap_existing_schema(cur, done, all_migrations)
        conn.commit()

    pending = [m for m in all_migrations if not is_applied(m, done)]
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
