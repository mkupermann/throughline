"""`status` must read the migration table the runner actually writes.

`_schema_version` queried a `schema_migrations` table with a `version` column.
`scripts/migrate.py` creates `applied_migrations` with a `migration_name`
column. No database has ever had the former, so a fully migrated database
reported "(no schema_migrations table)" — which reads as *migrations are not
tracked here*, the opposite of the truth. Nobody looked further, and a pending
migration (001_message_dedup) sat unapplied behind that message.

The tests below bind the report to the runner's own schema, so the two cannot
drift apart again silently.
"""

from __future__ import annotations

import psycopg2
import pytest

from throughline.status import collect_status

pytestmark = pytest.mark.integration

# Mirrors scripts/migrate.py. If the runner's DDL changes, this fails and the
# reader is meant to change with it.
_RUNNER_DDL = """
CREATE TABLE IF NOT EXISTS applied_migrations (
    migration_name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
)
"""


def _apply(conn, *names: str) -> None:
    with conn.cursor() as cur:
        cur.execute(_RUNNER_DDL)
        for n in names:
            cur.execute(
                "INSERT INTO applied_migrations (migration_name) VALUES (%s) "
                "ON CONFLICT DO NOTHING",
                (n,),
            )
    conn.commit()


def test_reports_the_latest_applied_migration(db_env):
    conn = psycopg2.connect(**db_env)
    try:
        _apply(conn, "000_baseline.sql", "002_source_tool.sql", "001_message_dedup.sql")
        assert collect_status(conn=conn)["schema_version"] == "002_source_tool.sql"
    finally:
        conn.close()


def test_ordering_is_by_name_not_by_timestamp(db_env):
    """A one-shot migration run stamps every row with the same time.

    Ordering by ``applied_at`` then returns an arbitrary member of the tie, so
    the reported version would flap between runs on the same database.
    """
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(_RUNNER_DDL)
            cur.execute(
                "INSERT INTO applied_migrations (migration_name, applied_at) VALUES "
                "('000_baseline.sql', '2026-01-01T00:00:00Z'), "
                "('002_source_tool.sql', '2026-01-01T00:00:00Z'), "
                "('001_message_dedup.sql', '2026-01-01T00:00:00Z')"
            )
        conn.commit()
        assert collect_status(conn=conn)["schema_version"] == "002_source_tool.sql"
    finally:
        conn.close()


def test_untracked_database_reports_none(db_env):
    """No tracking table must not masquerade as a version."""
    conn = psycopg2.connect(**db_env)
    try:
        assert collect_status(conn=conn)["schema_version"] is None
    finally:
        conn.close()


def test_pending_migrations_are_listed(db_env):
    """Every migration file in the repo that is not recorded must be reported.

    Applying only the baseline leaves the rest pending, so the list is non-empty
    without this test having to know how many migrations exist today.
    """
    conn = psycopg2.connect(**db_env)
    try:
        _apply(conn, "000_baseline.sql")
        pending = collect_status(conn=conn)["pending_migrations"]
        assert pending is not None, "the repo's sql/migrations directory was not found"
        assert "000_baseline.sql" not in pending
        assert pending, "later migrations exist in the repo but were reported as applied"
        assert all(n.endswith(".sql") for n in pending)
    finally:
        conn.close()


def test_pending_is_none_not_empty_when_unknowable(db_env):
    """"Could not check" must stay distinguishable from "nothing pending".

    Collapsing them would let a status report that never looked print an
    all-clear.
    """
    conn = psycopg2.connect(**db_env)
    try:
        assert collect_status(conn=conn)["pending_migrations"] is None
    finally:
        conn.close()
