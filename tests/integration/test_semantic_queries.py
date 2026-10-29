"""Behavioural tests for throughline.queries.semantic.

Deliberately no assertions on the query *plan*. Whether PostgreSQL picks the
HNSW index depends on table size and filter selectivity, so a plan assertion
flips between pass and fail as the fixture grows — it tests the planner, not
our code. See throughline/queries/semantic.py for the measurements that led to
that decision.

What is asserted instead is the property that a plan change would actually
break: a filtered search must still return the number of rows it was asked
for. The reverted "index pushdown" optimisation returned 2 rows where 20 were
requested, and only a test like `test_project_filter_still_fills_the_limit`
catches that.
"""

from __future__ import annotations

import pytest

from throughline.queries import semantic

pytestmark = pytest.mark.integration

DIM = 768
COLUMN = "embedding_768"
MODEL = "nomic-embed-text"
CHUNKS = 1200
#: Only every 30th chunk is in proj-a (40 of 1200). Chosen so the filter is
#: sharper than any plausible candidate cap: an implementation that probes
#: `limit * 10` rows before filtering keeps ~6 of them and fails the
#: fill-the-limit test below, while a correct one returns all 20.
PROJECT_EVERY = 30


@pytest.fixture()
def seeded(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_chunks (source_type, source_id, content, category, project_name)
            SELECT 'conversation', NULL, 'chunk ' || g, 'insight',
                   CASE WHEN g %% %s = 0 THEN 'proj-a' ELSE 'proj-b' END
            FROM generate_series(1, %s) g
            """,
            (PROJECT_EVERY, CHUNKS),
        )
        cur.execute(
            """
            INSERT INTO embeddings (source_type, source_id, embedding_768, model)
            SELECT 'memory_chunk', id,
                   (SELECT array_agg(random())::vector(768) FROM generate_series(1, %s)),
                   %s
            FROM memory_chunks
            """,
            (DIM, MODEL),
        )
        cur.execute("ANALYZE embeddings")
        cur.execute("ANALYZE memory_chunks")
    db_connection.commit()
    return db_connection


def _probe() -> str:
    return semantic.vec_literal([0.01] * DIM)


def test_returns_rows_in_distance_order(seeded):
    hits = semantic.semantic_search(seeded, _probe(), model=MODEL, column=COLUMN, limit=10)
    assert len(hits) == 10
    distances = [h["distance"] for h in hits]
    assert distances == sorted(distances)


def test_project_filter_restricts_results(seeded):
    hits = semantic.semantic_search(seeded, _probe(), model=MODEL, column=COLUMN, limit=10, project="proj-a")
    assert hits
    assert {h["project_name"] for h in hits} == {"proj-a"}


def test_project_filter_still_fills_the_limit(seeded):
    """A selective filter must not silently truncate the result set.

    proj-a holds CHUNKS/PROJECT_EVERY rows — far more than the limit — so a
    correct implementation returns exactly `limit` rows. An implementation
    that caps candidates *before* applying the filter returns far fewer.
    """
    available = CHUNKS // PROJECT_EVERY
    limit = 20
    assert available > limit, "fixture must have more matches than the limit"

    hits = semantic.semantic_search(seeded, _probe(), model=MODEL, column=COLUMN, limit=limit, project="proj-a")
    assert len(hits) == limit, (
        f"filtered search returned {len(hits)} of {limit} requested rows while "
        f"{available} matching chunks exist — candidates are being capped "
        "before the filter is applied"
    )


def test_unknown_embedding_column_is_rejected(seeded):
    with pytest.raises(ValueError):
        semantic.semantic_search(seeded, _probe(), model=MODEL, column="embedding_999; DROP TABLE x", limit=5)
    with pytest.raises(ValueError):
        semantic.similar_to_source(seeded, "memory_chunk", 1, model=MODEL, column="bogus", limit=5)


def test_similar_to_source_excludes_itself(seeded):
    row_id = next(
        r["source_id"] for r in semantic.semantic_search(seeded, _probe(), model=MODEL, column=COLUMN, limit=1)
    )
    hits = semantic.similar_to_source(seeded, "memory_chunk", row_id, model=MODEL, column=COLUMN, limit=5)
    assert hits
    assert all(h["source_id"] != row_id for h in hits)


def test_similar_to_source_on_missing_row_returns_empty(seeded):
    assert semantic.similar_to_source(seeded, "memory_chunk", 10**9, model=MODEL, column=COLUMN, limit=5) == []


def test_count_embeddings(seeded):
    assert semantic.count_embeddings(seeded, MODEL) == CHUNKS
    assert semantic.count_embeddings(seeded, "no-such-model") == 0
