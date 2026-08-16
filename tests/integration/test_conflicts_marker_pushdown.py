"""Equivalence + performance guard for the contradiction-marker pushdown.

`_semantic_conflicts` used to fetch every high-similarity pair in a
project+category group and then drop the ones whose newer side lacked a
contradiction marker — in Python, after the database had already computed a
768-dimensional distance for each pair. The marker test is now a join
predicate.

Measured on PostgreSQL 16 / pgvector 0.8.2:

    active chunks   before      after     speedup
    14,967          6.60 s      0.069 s      95x
    28,167          17.08 s     0.140 s     122x

That is only a valid optimisation if it selects exactly the same pairs, which
is what `test_pushdown_matches_python_filter` asserts against the pre-change
query shape.
"""

from __future__ import annotations

import pytest

from throughline import conflicts

pytestmark = pytest.mark.integration


#: The query as it was before the pushdown: marker test omitted from SQL,
#: applied afterwards in Python. Retained as the reference implementation.
LEGACY_SQL = """
    WITH chunk_vec AS (
        SELECT mc.id AS chunk_id, mc.project_name, mc.category::text AS category,
               mc.content AS full_content, mc.created_at,
               COALESCE(c.entrypoint, 'unknown') AS tool,
               e.embedding_768 AS vec
        FROM public.memory_chunks mc
        LEFT JOIN public.conversations c
            ON c.id = mc.source_id AND mc.source_type = 'conversation'
        JOIN public.embeddings e ON e.source_type = 'memory_chunk' AND e.source_id = mc.id
        WHERE mc.status = 'active' AND e.embedding_768 IS NOT NULL
          AND mc.category IN ('decision', 'pattern', 'insight')
    )
    SELECT a.chunk_id, b.chunk_id, b.full_content, 1 - (a.vec <=> b.vec) AS cosine_sim
    FROM chunk_vec a JOIN chunk_vec b
      ON a.project_name = b.project_name AND a.category = b.category
     AND a.tool <> b.tool AND a.chunk_id < b.chunk_id AND a.created_at <= b.created_at
    WHERE 1 - (a.vec <=> b.vec) >= %(min_sim)s
    ORDER BY cosine_sim DESC LIMIT 500
"""

NEW_SQL = """
    WITH chunk_vec AS (
        SELECT mc.id AS chunk_id, mc.project_name, mc.category::text AS category,
               mc.content AS full_content, mc.created_at,
               COALESCE(c.entrypoint, 'unknown') AS tool,
               e.embedding_768 AS vec
        FROM public.memory_chunks mc
        LEFT JOIN public.conversations c
            ON c.id = mc.source_id AND mc.source_type = 'conversation'
        JOIN public.embeddings e ON e.source_type = 'memory_chunk' AND e.source_id = mc.id
        WHERE mc.status = 'active' AND e.embedding_768 IS NOT NULL
          AND mc.category IN ('decision', 'pattern', 'insight')
    )
    SELECT a.chunk_id, b.chunk_id, b.full_content, 1 - (a.vec <=> b.vec) AS cosine_sim
    FROM chunk_vec b JOIN chunk_vec a
      ON a.project_name = b.project_name AND a.category = b.category
     AND a.tool <> b.tool AND a.chunk_id < b.chunk_id AND a.created_at <= b.created_at
     AND 1 - (a.vec <=> b.vec) >= %(min_sim)s
    WHERE b.full_content ~* %(marker_re)s
    ORDER BY cosine_sim DESC LIMIT 500
"""

#: Chunk bodies chosen to exercise the marker set: plain text, every marker
#: shape (single word, multi-word, hyphenated), and near-misses that must NOT
#: match so the two regex dialects are tested on word boundaries too.
BODIES = [
    "We use Postgres for the primary store",
    "We decided to use Redis instead of Memcached",
    "That approach is no longer correct",
    "The migration was rolled back last night",
    "We rolled-back the deploy",
    "Triggered a rollback of the schema change",
    "The change was reverted",
    "We switched from yarn to pnpm",
    "Actually the earlier note was wrong",
    "The proposal was rejected by the team",
    "This API is deprecated",
    "The plan was abandoned",
    "Module replaced by the new adapter",
    "This config is obsolete",
    "We are now using uv for installs",
    "We moved away from Celery",
    "This chunk supersedes the earlier one",
    # near-misses: substrings of markers inside longer words
    "The instead-of pattern was insteadful nonsense",
    "Rollbacks aside, the deployment succeeded",
    "Deprecating things is different from deprecated things",
    "An actuality is not a marker",
]

TOOLS = ["claude-code", "cursor", "windsurf", "codex"]


@pytest.fixture()
def seeded(db_connection):
    """A small corpus spanning several tools, projects and marker shapes."""
    with db_connection.cursor() as cur:
        for i, tool in enumerate(TOOLS):
            cur.execute(
                """
                INSERT INTO conversations
                    (session_id, project_path, entrypoint, source_tool, started_at, message_count)
                VALUES (gen_random_uuid(), %s, %s, %s, now() - make_interval(mins => %s), 10)
                RETURNING id
                """,
                (f"/repo/proj-{i % 2}", tool, tool, i),
            )
            conv_id = cur.fetchone()[0]
            for j, body in enumerate(BODIES):
                # source_type='conversation', source_id=conv_id mirrors real
                # extraction (scripts/extract_memory.py inserts memory_chunks
                # this way — source_id is a conversations.id, never a
                # messages.id). A conversation produces several chunks, so
                # several chunks legitimately share one source_id here, same
                # as in production.
                cur.execute(
                    """
                    INSERT INTO memory_chunks
                        (source_type, source_id, content, category, project_name, status, created_at)
                    VALUES ('conversation', %s, %s, %s, %s, 'active',
                            now() - make_interval(days => %s))
                    RETURNING id
                    """,
                    (
                        conv_id,
                        body,
                        ["decision", "pattern", "insight"][j % 3],
                        f"proj-{i % 2}",
                        (len(BODIES) - j) + i,
                    ),
                )
                chunk_id = cur.fetchone()[0]
                # Near-identical vectors so most pairs clear the similarity
                # floor — this maximises the pair count the legacy query has
                # to materialise, which is the situation being fixed.
                cur.execute(
                    """
                    INSERT INTO embeddings (source_type, source_id, embedding_768, model)
                    SELECT 'memory_chunk', %s,
                           (SELECT array_agg(0.5 + (random() - 0.5) * 0.01)::vector(768)
                            FROM generate_series(1, 768)),
                           'nomic-embed-text'
                    """,
                    (chunk_id,),
                )
        cur.execute("ANALYZE memory_chunks")
        cur.execute("ANALYZE embeddings")
    db_connection.commit()
    return db_connection


def _run(conn, sql, **extra):
    params = {"min_sim": 0.5, **extra}
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_sql_and_python_markers_agree_on_every_body(seeded):
    """The `~*` predicate and `_CONTRADICTION_RE` must classify identically.

    Two regex dialects (Python `\\b`, Postgres `\\m`/`\\M`) built from one
    marker list — the failure mode is a silent divergence on word boundaries,
    so the corpus deliberately includes near-misses.
    """
    with seeded.cursor() as cur:
        cur.execute(
            "SELECT b, b ~* %s FROM unnest(%s::text[]) AS b",
            (conflicts._CONTRADICTION_SQL_RE, BODIES),
        )
        sql_verdicts = {body: bool(hit) for body, hit in cur.fetchall()}

    mismatches = {
        body: (sql_hit, conflicts._has_contradiction_marker(body))
        for body, sql_hit in sql_verdicts.items()
        if sql_hit != conflicts._has_contradiction_marker(body)
    }
    assert not mismatches, f"SQL and Python marker predicates disagree (body: sql_says, python_says): {mismatches}"


def test_pushdown_matches_python_filter(seeded):
    """The optimised query must select exactly the legacy pair set.

    Legacy = all high-similarity pairs, then filtered in Python.
    New     = marker applied as a join predicate.
    """
    legacy = {
        (a, b) for a, b, full_b, _sim in _run(seeded, LEGACY_SQL) if conflicts._has_contradiction_marker(full_b or "")
    }
    new = {(a, b) for a, b, _full_b, _sim in _run(seeded, NEW_SQL, marker_re=conflicts._CONTRADICTION_SQL_RE)}

    assert legacy, "fixture produced no conflicts — it cannot discriminate"
    assert new == legacy, (
        f"pushdown changed the result set: missing={sorted(legacy - new)[:10]} extra={sorted(new - legacy)[:10]}"
    )


def test_pushdown_examines_fewer_pairs(seeded):
    """The point of the change: fewer rows materialised before filtering."""
    with seeded.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + LEGACY_SQL, {"min_sim": 0.5})
        legacy_plan = cur.fetchone()[0]
        cur.execute(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + NEW_SQL,
            {"min_sim": 0.5, "marker_re": conflicts._CONTRADICTION_SQL_RE},
        )
        new_plan = cur.fetchone()[0]

    def total_rows(node) -> int:
        n = int(node.get("Actual Rows", 0)) * max(1, int(node.get("Actual Loops", 1)))
        return n + sum(total_rows(child) for child in node.get("Plans", []))

    legacy_rows = total_rows(legacy_plan[0]["Plan"])
    new_rows = total_rows(new_plan[0]["Plan"])
    assert new_rows < legacy_rows, f"pushdown did not reduce materialised rows: {new_rows} vs {legacy_rows}"


def test_find_conflicts_still_reports_semantic_conflicts(seeded):
    """End-to-end: the public entry point still surfaces semantic conflicts."""
    with seeded.cursor() as cur:
        found = conflicts._semantic_conflicts(cur, project=None, min_similarity=0.5)
    assert found, "expected semantic conflicts from the seeded corpus"
    for c in found:
        assert c.kind == "semantic"
        assert c.a.tool != c.b.tool
        assert c.a.category == c.b.category
        assert 0.0 <= c.confidence <= 1.0
