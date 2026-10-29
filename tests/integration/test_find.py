"""Tests for the unified /find retrieval layer.

The headline property is **recall against ground truth**. The implementation
this replaced used `similarity(content, term)`, which returned 8 of 474 true
matches on the real corpus — a search that looks like it works and quietly
isn't. Recall is therefore asserted directly against an ILIKE oracle rather
than against a golden result list.
"""

from __future__ import annotations

import pytest

from throughline.queries import find as F

pytestmark = pytest.mark.integration

#: Long enough that a naive whole-string similarity score collapses toward
#: zero — which is exactly the failure mode being guarded against.
FILLER = (
    "This is a long assistant message with a great deal of surrounding prose "
    "that has nothing whatever to do with the search term. " * 60
)


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO conversations
                (session_id, project_path, model, entrypoint, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/repo/alpha', 'claude', 'claude-code',
                    now() - interval '2 days', 4, 'Postgres migration notes')
            RETURNING id
            """)
        conv = cur.fetchone()[0]

        # The term sits AFTER the ranking prefix, so it can only be found if
        # membership is decided by a substring filter rather than by score.
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES "
            "(%s, 'assistant', %s, now()), "
            "(%s, 'user', %s, now()), "
            "(%s, 'assistant', %s, now())",
            (
                conv,
                f"{FILLER} the pgvector extension is broken",
                conv,
                "short message mentioning pgvector up front",
                conv,
                "a message about something else entirely",
            ),
        )
        cur.execute(
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, tags, confidence, project_name, status)
            VALUES
                ('conversation', %s, %s, 'decision', ARRAY['db'], 0.9, 'alpha', 'active'),
                ('conversation', %s, 'pgvector is pinned to 0.8', 'pattern', ARRAY['pgvector'], 0.8, 'beta', 'active'),
                ('conversation', %s, 'unrelated content here', 'insight', ARRAY['misc'], 0.4, 'alpha', 'active')
            """,
            (conv, f"{FILLER} we chose pgvector for similarity search", conv, conv),
        )
        cur.execute(
            "INSERT INTO skills (name, description, path) VALUES "
            "('pgvector-helper', 'works with pgvector indexes', '/s/pgv')"
        )
        cur.execute("INSERT INTO projects (name, description, status) VALUES ('alpha', 'uses pgvector', 'active')")
        cur.execute(
            "INSERT INTO prompts (name, category, content) VALUES "
            "('pgvector audit', 'review', 'check the pgvector install')"
        )
        cur.execute("ANALYZE")
    db_connection.commit()
    return db_connection


def _ilike_truth(conn, table: str, column: str, term: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE {column} ILIKE %s", (f"%{term}%",))
        return int(cur.fetchone()[0])


def test_recall_matches_ilike_ground_truth_for_messages(corpus):
    """Every message containing the term must be findable.

    This is the regression that matters: the previous scorer missed matches
    buried past the start of a long document.
    """
    truth = _ilike_truth(corpus, "messages", "content", "pgvector")
    assert truth >= 2, "fixture must contain matching messages"

    res = F.find(corpus, "pgvector", filters=F.FindFilters(kinds=["message"]), limit=100)
    assert res.total == truth, f"found {res.total} of {truth} matching messages — recall regression"


def test_finds_a_term_buried_past_the_ranking_prefix(corpus):
    """A match beyond RANK_PREFIX still has to appear in the result set."""
    res = F.find(corpus, "pgvector", filters=F.FindFilters(kinds=["message"]), limit=100)
    snippets = [(r["snippet"] or "") for r in res.items]
    assert any(
        s.startswith("This is a long assistant message") for s in snippets
    ), "the long message whose match sits past the prefix was dropped"


def test_prominent_match_outranks_buried_match(corpus):
    """Ranking still has to mean something: early mention beats late mention."""
    res = F.find(corpus, "pgvector", filters=F.FindFilters(kinds=["message"]), limit=100)
    order = [(r["snippet"] or "")[:40] for r in res.items]
    short_idx = next(i for i, s in enumerate(order) if s.startswith("short message"))
    long_idx = next(i for i, s in enumerate(order) if s.startswith("This is a long"))
    assert short_idx < long_idx, "a term in the opening line should rank above one buried deep"


def test_searches_every_record_type(corpus):
    res = F.find(corpus, "pgvector", limit=100)
    kinds = {r["kind"] for r in res.items}
    assert {"message", "memory", "skill", "project", "prompt"} <= kinds, f"unified search missed record types: {kinds}"


def test_result_shape_is_uniform_across_kinds(corpus):
    """Fusion depends on every retriever returning the same columns."""
    expected = {
        "kind",
        "id",
        "title",
        "snippet",
        "project",
        "occurred_at",
        "category",
        "status",
        "confidence",
        "conversation_id",
        "score",
        "retrievers",
    }
    for item in F.find(corpus, "pgvector", limit=100).items:
        assert set(item) == expected, f"{item['kind']} has a different shape: {set(item) ^ expected}"


def test_lexical_only_when_no_backend_and_says_so(corpus):
    res = F.find(corpus, "pgvector", limit=10)
    assert res.modes == ["lexical"]
    assert any(
        "Semantic search is off" in n for n in res.notes
    ), "a degraded search must explain itself, not silently return less"


def test_empty_query_returns_nothing(corpus):
    for q in ("", "   ", None):
        res = F.find(corpus, q, limit=10)
        assert res.items == [] and res.total == 0


def test_kind_filter_restricts_results(corpus):
    res = F.find(corpus, "pgvector", filters=F.FindFilters(kinds=["memory"]), limit=100)
    assert res.items
    assert {r["kind"] for r in res.items} == {"memory"}


def test_category_and_project_filters(corpus):
    res = F.find(
        corpus,
        "pgvector",
        filters=F.FindFilters(kinds=["memory"], categories=["pattern"]),
        limit=100,
    )
    assert {r["category"] for r in res.items} == {"pattern"}

    res = F.find(
        corpus,
        "pgvector",
        filters=F.FindFilters(kinds=["memory"], projects=["beta"]),
        limit=100,
    )
    assert {r["project"] for r in res.items} == {"beta"}


def test_tag_match_ranks_highly(corpus):
    """An exact tag hit is a strong signal and should not be buried."""
    res = F.find(corpus, "pgvector", filters=F.FindFilters(kinds=["memory"]), limit=100)
    tagged = next(r for r in res.items if r["project"] == "beta")
    assert res.items.index(tagged) == 0


def test_pagination_is_stable(corpus):
    """Offsetting must slice one ranking, not reshuffle it."""
    full = F.find(corpus, "pgvector", limit=100).items
    page1 = F.find(corpus, "pgvector", limit=2, offset=0).items
    page2 = F.find(corpus, "pgvector", limit=2, offset=2).items

    def keys(rs):
        return [(r["kind"], r["id"]) for r in rs]

    assert keys(page1) == keys(full[:2])
    assert keys(page2) == keys(full[2:4])


def test_rrf_rewards_agreement_between_retrievers():
    """A document both retrievers rank should beat one only one found."""
    a = [{"kind": "memory", "id": 1, "occurred_at": None}, {"kind": "memory", "id": 2, "occurred_at": None}]
    b = [{"kind": "memory", "id": 2, "occurred_at": None}, {"kind": "memory", "id": 3, "occurred_at": None}]
    fused = F._rrf([a, b])
    assert fused[0]["id"] == 2, "the document both retrievers found should rank first"
    assert fused[0]["retrievers"] == 2


def test_facets_report_available_values(corpus):
    fx = F.facets(corpus)
    assert set(fx) == {"kinds", "categories", "statuses", "projects", "tags"}
    assert {k["value"] for k in fx["kinds"]} == set(F.KINDS)
    assert "alpha" in {p["value"] for p in fx["projects"]}
    assert all(isinstance(c["n"], int) for c in fx["categories"])
