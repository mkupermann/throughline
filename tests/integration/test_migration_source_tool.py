"""The source_tool backfill: correct per rule, idempotent, honest about NULL."""

from __future__ import annotations

from pathlib import Path

import pytest

from throughline import providers as P

pytestmark = pytest.mark.integration

MIGRATION = Path(__file__).resolve().parents[2] / "throughline" / "migrations" / "002_source_tool.sql"


def _apply(conn):
    with conn.cursor() as cur:
        cur.execute(MIGRATION.read_text())
    conn.commit()


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS source_tool")
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, entrypoint, started_at, message_count, metadata)
            VALUES
                (gen_random_uuid(), '/a', 'sdk-cli',      now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/b', 'cli',          now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/c', 'windsurf',     now(), 1, '{"source":"windsurf"}'::jsonb),
                (gen_random_uuid(), '/d', 'continue.dev', now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/e', 'zed',          now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/f', '',             now(), 1, '{}'::jsonb),
                (gen_random_uuid(), '/g', NULL,           now(), 1, '{}'::jsonb)
            """
        )
    db_connection.commit()
    return db_connection


def _tool(conn, project_path: str):
    with conn.cursor() as cur:
        cur.execute("SELECT source_tool FROM conversations WHERE project_path=%s", (project_path,))
        return cur.fetchone()[0]


def test_claude_code_entrypoints_become_claude_code(corpus):
    """Bar 1: the 3,016 rows report claude_code, not cli/sdk-cli."""
    _apply(corpus)
    assert _tool(corpus, "/a") == "claude_code"
    assert _tool(corpus, "/b") == "claude_code"


def test_metadata_source_wins_when_it_names_a_known_adapter(corpus):
    _apply(corpus)
    assert _tool(corpus, "/c") == "windsurf"


def test_continue_dev_maps_to_the_adapter_name(corpus):
    _apply(corpus)
    assert _tool(corpus, "/d") == "continue"


def test_entrypoint_matching_an_adapter_is_taken_literally(corpus):
    _apply(corpus)
    assert _tool(corpus, "/e") == "zed"


def test_genuinely_unknown_rows_stay_null(corpus):
    """Spec §3.3: labelling these would be a fabrication that hardens into fact."""
    _apply(corpus)
    assert _tool(corpus, "/f") is None
    assert _tool(corpus, "/g") is None


def test_every_backfilled_value_is_a_registered_provider(corpus):
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute("SELECT DISTINCT source_tool FROM conversations WHERE source_tool IS NOT NULL")
        found = {r[0] for r in cur.fetchall()}
    assert found <= P.NAMES


def test_rerunning_the_migration_changes_zero_rows(corpus):
    """Bar 2. Guarded by `source_tool IS NULL` on every UPDATE."""
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute("SELECT id, source_tool FROM conversations ORDER BY id")
        before = cur.fetchall()
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute("SELECT id, source_tool FROM conversations ORDER BY id")
        after = cur.fetchall()
    assert before == after


def test_the_column_is_indexed(corpus):
    _apply(corpus)
    with corpus.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename='conversations' AND indexname='idx_conversations_source_tool'"
        )
        assert cur.fetchone() is not None
