"""Browse mode, Timeline data sources, and the result-set graph."""

from __future__ import annotations

import pytest

from throughline.queries import entities as E
from throughline.queries import find as F

pytestmark = pytest.mark.integration


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, model, entrypoint, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/repo/alpha', 'claude', 'claude-code',
                    now() - interval '3 days', 3, 'alpha work')
            RETURNING id
            """
        )
        conv = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "SELECT %s, 'assistant', 'msg ' || g, now() - (g || ' days')::interval "
            "FROM generate_series(1, 5) g",
            (conv,),
        )
        cur.execute(
            """
            INSERT INTO memory_chunks (source_type, source_id, content, category, project_name, created_at)
            SELECT 'conversation', %s, 'chunk ' || g, 'insight', 'alpha', now() - (g || ' days')::interval
            FROM generate_series(1, 5) g
            """,
            (conv,),
        )
        cur.execute("INSERT INTO skills (name, description, path) VALUES ('s1','d','/p')")
        cur.execute("INSERT INTO projects (name, description, status) VALUES ('alpha','d','active')")
        cur.execute("INSERT INTO prompts (name, category, content) VALUES ('p1','review','c')")
        cur.execute(
            "INSERT INTO entities (entity_type, name, canonical_name, project_name, mention_count) "
            "VALUES ('technology','Postgres','postgres','alpha',5), "
            "       ('person','Alex','alex','alpha',2), "
            "       ('concept','Unrelated','unrelated','beta',9) RETURNING id"
        )
        cur.execute("SELECT id FROM entities ORDER BY id")
        ents = [r[0] for r in cur.fetchall()]
        cur.execute(
            "INSERT INTO relationships (from_entity, to_entity, relation_type) VALUES (%s,%s,'uses')",
            (ents[0], ents[1]),
        )
        # Only the first two entities are mentioned by our conversation.
        cur.execute(
            "INSERT INTO entity_mentions (entity_id, source_type, source_id) VALUES (%s,'conversation',%s),(%s,'conversation',%s)",
            (ents[0], conv, ents[1], conv),
        )
    db_connection.commit()
    return {"conversation": conv, "entities": ents}


def test_browse_returns_results_without_a_query(corpus, db_connection):
    res = F.browse(db_connection, F.FindFilters(kinds=["memory"]), limit=10)
    assert res.total > 0
    assert res.modes == ["browse"]


def test_browse_is_newest_first(corpus, db_connection):
    items = F.browse(db_connection, F.FindFilters(kinds=["memory"]), limit=10).items
    dates = [i["occurred_at"] for i in items]
    assert dates == sorted(dates, reverse=True)


def test_browse_pagination_is_stable_and_disjoint(corpus, db_connection):
    """Ties on timestamp must not reshuffle between page sizes.

    The merge takes the top-N of each kind; if SQL and the merge disagree on
    how to break ties, page 2 repeats rows from page 1.
    """

    def key(rs):
        return [(r["kind"], r["id"]) for r in rs]

    p1 = key(F.browse(db_connection, F.FindFilters(), limit=4, offset=0).items)
    p2 = key(F.browse(db_connection, F.FindFilters(), limit=4, offset=4).items)
    both = key(F.browse(db_connection, F.FindFilters(), limit=8, offset=0).items)
    assert not set(p1) & set(p2), "pages overlap"
    assert p1 + p2 == both, "paging does not match a single larger page"


def test_browse_covers_every_timeline_source(corpus, db_connection):
    """Timeline parity: all six find-able record types must be browsable.

    (Entities and reflections, the other two sources the old Calendar page
    assembled, have their own routes — entities via the graph view.)
    """
    res = F.browse(db_connection, F.FindFilters(), limit=500)
    kinds = {i["kind"] for i in res.items}
    assert kinds == set(F.KINDS), f"missing timeline sources: {set(F.KINDS) - kinds}"


def test_browse_respects_filters(corpus, db_connection):
    res = F.browse(db_connection, F.FindFilters(kinds=["memory"], projects=["alpha"]), limit=50)
    assert res.items
    assert {i["project"] for i in res.items} == {"alpha"}


def test_browse_warns_when_capped(corpus, db_connection):
    res = F.browse(db_connection, F.FindFilters(kinds=["memory"]), limit=2)
    assert any("most recent" in n for n in res.notes), (
        "a capped listing must say so rather than presenting a partial total as complete"
    )


def test_graph_is_limited_to_the_given_sources(corpus, db_connection):
    """The graph must never be the whole graph."""
    graph = E.subgraph_for_sources(db_connection, [("conversation", corpus["conversation"])])
    names = {n["name"] for n in graph["nodes"]}
    assert names == {"Postgres", "Alex"}, f"graph leaked unrelated entities: {names}"
    assert "Unrelated" not in names


def test_graph_edges_are_induced(corpus, db_connection):
    graph = E.subgraph_for_sources(db_connection, [("conversation", corpus["conversation"])])
    node_ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["from_entity"] in node_ids and edge["to_entity"] in node_ids


def test_graph_counts_hits_in_results(corpus, db_connection):
    graph = E.subgraph_for_sources(db_connection, [("conversation", corpus["conversation"])])
    assert all(n["hits_in_results"] >= 1 for n in graph["nodes"])


def test_graph_of_nothing_is_empty_not_everything(corpus, db_connection):
    assert E.subgraph_for_sources(db_connection, []) == {"nodes": [], "edges": []}


def test_graph_node_cap_is_respected(corpus, db_connection):
    graph = E.subgraph_for_sources(db_connection, [("conversation", corpus["conversation"])], limit_nodes=1)
    assert len(graph["nodes"]) == 1
