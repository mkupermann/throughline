"""`source_type` is a closed vocabulary and the database must say so.

`throughline status` filtered on `source_type IN ('extraction', 'mcp_write')`
for as long as that code existed. Neither value has ever been written, so the
filter matched nothing, `max()` over the empty set returned NULL, and the
indicator printed "—" on a database holding 986 chunks — a stalled extractor
and a typo looked identical. Free text is what made the typo survivable.

These tests bind the constraint to the values the code writes, from both
directions: a spelling nobody writes must be rejected, and every spelling the
code does write must be accepted.
"""

from __future__ import annotations

import psycopg2
import pytest

from throughline.status import _EXTRACTION_SOURCE_TYPES

pytestmark = pytest.mark.integration

#: Mirrors sql/migrations/003_source_type_vocabularies.sql.
MEMORY_CHUNK_TYPES = ("conversation", "manual", "mcp_write", "reflection_merge", "consolidation")
EMBEDDING_TYPES = ("memory_chunk", "message")


@pytest.mark.parametrize("source_type", MEMORY_CHUNK_TYPES)
def test_every_written_chunk_type_is_accepted(db_env, source_type):
    """A constraint that rejects a value the code writes breaks ingestion."""
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_chunks (source_type, content, category) VALUES (%s, 'x', 'insight')",
                (source_type,),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["extraction", "Conversation", "conversations", "", "x"])
def test_unknown_chunk_types_are_rejected(db_env, bad):
    """Including the exact spelling that caused the original bug.

    'Conversation' and 'conversations' are here because a near-miss is the
    realistic mistake — an exact-match filter treats them as different types
    while a human reading the code sees no difference.
    """
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO memory_chunks (source_type, content, category) VALUES (%s, 'x', 'insight')",
                (bad,),
            )
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["memory_chunks", "chunk", "conversation"])
def test_unknown_embedding_types_are_rejected(db_env, bad):
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO embeddings (source_type, source_id, model) VALUES (%s, 1, 'm')",
                (bad,),
            )
    finally:
        conn.close()


def test_status_constant_stays_inside_the_vocabulary():
    """The freshness indicator must not filter on a value the schema forbids.

    This is the guard the original bug lacked: the constant and the column's
    permitted values are maintained in different files, so nothing but a test
    notices when one moves.
    """
    unknown = set(_EXTRACTION_SOURCE_TYPES) - set(MEMORY_CHUNK_TYPES)
    assert not unknown, (
        f"status filters on {sorted(unknown)}, which the CHECK constraint forbids — "
        "the indicator can never fire for those"
    )
