"""End-to-end test: insert memory chunks directly and query them back."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration


def _insert_chunk(
    cur,
    content: str,
    category: str,
    tags: list[str] | None = None,
    confidence: float = 0.9,
    project: str = "demo-project",
    status: str = "active",
) -> int:
    cur.execute(
        """
        INSERT INTO memory_chunks
            (source_type, source_id, content, category, tags, confidence,
             project_name, status, created_at)
        VALUES (%s, %s, %s, %s::memory_category, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            "conversation",
            None,
            content,
            category,
            tags or [],
            confidence,
            project,
            status,
            datetime.now(tz=timezone.utc),
        ),
    )
    return cur.fetchone()[0]


def test_insert_and_filter_by_category(db_connection):
    with db_connection.cursor() as cur:
        _insert_chunk(cur, "We chose Postgres over SQLite.", "decision", ["architecture"])
        _insert_chunk(cur, "Always use parameterized queries.", "pattern", ["security"])
        _insert_chunk(cur, "User prefers dark mode.", "preference")
        db_connection.commit()

        cur.execute(
            "SELECT content FROM memory_chunks WHERE category = 'decision'"
        )
        rows = [r[0] for r in cur.fetchall()]
        assert rows == ["We chose Postgres over SQLite."]

        cur.execute("SELECT count(*) FROM memory_chunks")
        assert cur.fetchone()[0] == 3


def test_tag_array_contains_lookup(db_connection):
    with db_connection.cursor() as cur:
        _insert_chunk(cur, "Use ivfflat for vectors under 1M rows.", "insight", ["pgvector", "ops"])
        _insert_chunk(cur, "HNSW is the default for recall.", "insight", ["pgvector"])
        _insert_chunk(cur, "Backups nightly via pg_dump.", "workflow", ["ops"])
        db_connection.commit()

        cur.execute(
            "SELECT content FROM memory_chunks "
            "WHERE tags @> ARRAY[%s]::text[] ORDER BY id",
            ("pgvector",),
        )
        results = [r[0] for r in cur.fetchall()]
        assert len(results) == 2
        assert "ivfflat" in results[0] or "ivfflat" in results[1]


def test_supersede_chain(db_connection):
    """A newer chunk can supersede an older one and we can chain the lookup."""
    with db_connection.cursor() as cur:
        old_id = _insert_chunk(cur, "Embedding dim is 1536.", "decision", ["embeddings"])
        new_id = _insert_chunk(cur, "Embedding dim is 768 (Ollama).", "decision", ["embeddings"])

        cur.execute(
            "UPDATE memory_chunks SET superseded_by = %s, superseded_at = now(), status = 'superseded' "
            "WHERE id = %s",
            (new_id, old_id),
        )
        db_connection.commit()

        cur.execute(
            "SELECT content FROM memory_chunks WHERE status = 'active' AND tags @> ARRAY['embeddings']::text[]"
        )
        current = [r[0] for r in cur.fetchall()]
        assert current == ["Embedding dim is 768 (Ollama)."]

        cur.execute(
            "SELECT m1.content AS old, m2.content AS new "
            "FROM memory_chunks m1 JOIN memory_chunks m2 ON m1.superseded_by = m2.id "
            "WHERE m1.id = %s",
            (old_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0].startswith("Embedding dim is 1536")
        assert row[1].startswith("Embedding dim is 768")


def test_trigram_search_on_content(db_connection):
    """pg_trgm is part of the baseline schema; similarity should work."""
    with db_connection.cursor() as cur:
        _insert_chunk(cur, "Deployed the streamlit dashboard yesterday.", "workflow")
        _insert_chunk(cur, "Streamlight bulbs replaced in the office.", "preference")
        _insert_chunk(cur, "Postgres is our system of record.", "decision")
        db_connection.commit()

        # Rank all rows by trigram similarity; the streamlit-dashboard row
        # should rank first. This avoids depending on pg_trgm.similarity_threshold
        # (default 0.3) which is too strict for a short keyword vs long sentences.
        cur.execute(
            "SELECT content, similarity(content, %s) AS sim "
            "FROM memory_chunks "
            "ORDER BY sim DESC",
            ("streamlit",),
        )
        hits = cur.fetchall()
        assert hits, "expected at least one row"
        top_content, top_sim = hits[0]
        assert "streamlit dashboard" in top_content.lower(), (
            f"top hit was {top_content!r} with sim={top_sim}"
        )
        assert top_sim > 0, f"top similarity should be >0, got {top_sim}"
