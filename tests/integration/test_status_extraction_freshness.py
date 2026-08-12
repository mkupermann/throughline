"""The extraction-freshness indicator must be able to fire.

``status`` reported "Last extraction at: —" against a database holding 986
memory chunks, because the query filtered on ``source_type IN ('extraction',
'mcp_write')`` and neither value has ever been written. ``max()`` over an empty
set is NULL rather than an error, and the column is plain ``text`` with no
constraint, so nothing anywhere rejected the mistake. A stalled extractor and a
mistyped filter looked identical from the outside.

These tests pin the property that was missing: for every source type the
codebase actually writes, a chunk carrying it must move the timestamp.
"""

from __future__ import annotations

import psycopg2
import pytest

from throughline.status import _EXTRACTION_SOURCE_TYPES, collect_status

pytestmark = pytest.mark.integration


def _insert_chunk(conn, source_type: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memory_chunks (source_type, content, category, confidence) "
            "VALUES (%s, %s, 'decision', 0.9)",
            (source_type, f"chunk written as {source_type}"),
        )
    conn.commit()


@pytest.mark.parametrize("source_type", _EXTRACTION_SOURCE_TYPES)
def test_each_extraction_source_type_moves_the_timestamp(db_env, source_type):
    """Every declared source type must be one the indicator recognises.

    Parametrised rather than looped so a single stale entry names itself in the
    failure output instead of hiding behind whichever sibling still works.
    """
    conn = psycopg2.connect(**db_env)
    try:
        before = collect_status(conn=conn)
        assert before.get("last_extraction_at") is None, (
            "fresh database should have no extraction timestamp"
        )

        _insert_chunk(conn, source_type)

        after = collect_status(conn=conn)
        assert after.get("last_extraction_at") is not None, (
            f"a chunk with source_type={source_type!r} left the indicator empty — "
            "either the constant is stale or the query no longer matches it"
        )
    finally:
        conn.close()


def test_reflection_output_does_not_count_as_extraction(db_env):
    """Reorganised memory must not make a stalled extractor look fresh.

    The reflection job merges and consolidates chunks that already exist. If its
    output counted, the indicator would keep ticking on its schedule while
    extraction had produced nothing for weeks — the exact failure this indicator
    exists to reveal.
    """
    conn = psycopg2.connect(**db_env)
    try:
        _insert_chunk(conn, "reflection_merge")
        _insert_chunk(conn, "consolidation")

        assert collect_status(conn=conn).get("last_extraction_at") is None, (
            "reflection/consolidation output must not register as extraction"
        )
    finally:
        conn.close()
