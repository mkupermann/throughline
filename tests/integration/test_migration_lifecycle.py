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


def _exists(conn, relation: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (relation,))
        return cur.fetchone()[0]


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
    paths["001_widen_conversation_token_counts.sql"] = paths["005_widen_conversation_token_counts.sql"]
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


def test_dry_run_never_bootstraps_an_untracked_schema(test_db) -> None:
    """Planning against the old Compose schema must leave every table untouched."""
    conn = psycopg2.connect(**test_db)
    try:
        assert not _exists(conn, "public.applied_migrations")

        assert migrate.cmd_migrate(conn, dry_run=True) == 0

        assert not _exists(conn, "public.applied_migrations")
    finally:
        conn.close()


def test_tracked_old_schema_applies_each_pending_migration_in_order(empty_test_db) -> None:
    """A real 001-era database reaches the current schema through pending files."""
    conn = psycopg2.connect(**empty_test_db)
    paths = {path.name: path for path in migrate.discover_migrations()}
    try:
        with conn.cursor() as cur:
            cur.execute(migrate.TRACKING_DDL)
        conn.commit()

        for name in ("000_baseline.sql", "001_message_dedup.sql"):
            with conn.cursor() as cur:
                migrate.run_migration(cur, paths[name])
            conn.commit()

        assert _names(conn) == {"000_baseline.sql", "001_message_dedup.sql"}
        assert migrate.cmd_migrate(conn, dry_run=False) == 0
        assert _names(conn) == {path.name for path in migrate.discover_migrations()}
    finally:
        conn.close()


def test_untracked_compose_schema_bootstraps_then_applies_pending_migrations(test_db) -> None:
    """The former schema.sql initialization path converges through the runner."""
    conn = psycopg2.connect(**test_db)
    try:
        assert not _exists(conn, "public.applied_migrations")

        assert migrate.cmd_migrate(conn, dry_run=False) == 0

        assert _names(conn) == {path.name for path in migrate.discover_migrations()}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'conversations' "
                "AND column_name = 'token_count_in'"
            )
            assert cur.fetchone() == ("bigint",)
    finally:
        conn.close()


def test_failed_migration_rolls_back_schema_and_tracking_row(empty_test_db, tmp_path) -> None:
    """A migration body and its tracking record are one database transaction."""
    migration_path = tmp_path / "010_atomicity_probe.sql"
    migration_path.write_text(
        "CREATE TABLE public.atomicity_probe (id integer);\nSELECT missing_function();\n",
        encoding="utf-8",
    )
    conn = psycopg2.connect(**empty_test_db)
    try:
        with conn.cursor() as cur:
            cur.execute(migrate.TRACKING_DDL)
        conn.commit()

        with pytest.raises(psycopg2.Error):
            with conn.cursor() as cur:
                migrate.run_migration(cur, migration_path)
        conn.rollback()

        assert not _exists(conn, "public.atomicity_probe")
        assert _names(conn) == set()
    finally:
        conn.close()
