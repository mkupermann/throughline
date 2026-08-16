"""Live PostgreSQL proof for fresh installs and existing-database upgrades."""

from __future__ import annotations

import psycopg2
import pytest

from throughline.jobs import migrate

pytestmark = pytest.mark.integration


def _names(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT migration_name FROM public.applied_migrations")
        return {row[0] for row in cur.fetchall()}


def test_fresh_database_applies_every_migration(empty_test_db) -> None:
    """An empty PostgreSQL database reaches the current schema through the runner."""
    conn = psycopg2.connect(**empty_test_db)
    try:
        assert migrate.cmd_migrate(conn, dry_run=False) == 0

        assert _names(conn) == {path.name for path in migrate.discover_migrations()}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'conversations' "
                "AND column_name = 'token_count_in'"
            )
            assert cur.fetchone() == ("bigint",)
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'conversations' "
                "AND column_name IN ('source_tool', 'generated_by') ORDER BY column_name"
            )
            assert cur.fetchall() == [("generated_by",), ("source_tool",)]
    finally:
        conn.close()


def test_historical_duplicate_filename_upgrades_without_replaying_it(empty_test_db) -> None:
    """A database that recorded the old duplicate name remains up to date safely."""
    historical_names = (
        "000_baseline.sql",
        "001_message_dedup.sql",
        "001_widen_conversation_token_counts.sql",
        "002_source_tool.sql",
        "003_source_type_vocabularies.sql",
        "004_generated_by.sql",
    )
    paths = {path.name: path for path in migrate.MIGRATIONS_DIR.glob("*.sql")}
    paths["001_widen_conversation_token_counts.sql"] = paths[
        "005_widen_conversation_token_counts.sql"
    ]
    conn = psycopg2.connect(**empty_test_db)
    try:
        with conn.cursor() as cur:
            cur.execute(migrate.TRACKING_DDL)
        conn.commit()

        for name in historical_names:
            migration = paths[name]
            with conn.cursor() as cur:
                migrate.run_migration(cur, migration, migration_name=name)
            conn.commit()

        assert migrate.cmd_migrate(conn, dry_run=False) == 0
        assert _names(conn) == set(historical_names)
    finally:
        conn.close()
