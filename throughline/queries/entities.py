"""Knowledge-graph queries over ``entities`` / ``relationships`` / ``entity_mentions``.

The GUI version of these queries built ``IN (...)`` lists by interpolating a
formatted Python tuple into the SQL string, with a special case for the
one-element tuple (``(1,)`` is not valid SQL). Every list here uses
``= ANY(%s)`` instead: psycopg2 adapts a Python list to a Postgres array, so
there is no string building, no one-element special case, and no injection
surface if the ids ever stop being trusted integers.
"""

from __future__ import annotations

from typing import Any

from ._exec import Row, one, rows, scalar


def graph_stats(conn) -> Row:
    return one(
        conn,
        """
        SELECT
            (SELECT count(*) FROM entities)         AS ents,
            (SELECT count(*) FROM relationships)    AS rels,
            (SELECT count(*) FROM entity_mentions)  AS ments,
            (SELECT count(DISTINCT source_id) FROM entity_mentions
             WHERE source_type = 'conversation')    AS convs_analyzed
        """,
    ) or {"ents": 0, "rels": 0, "ments": 0, "convs_analyzed": 0}


def pending_extraction_count(conn, min_messages: int = 3) -> int:
    return int(
        scalar(
            conn,
            """
            SELECT count(*) FROM conversations c
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_mentions em
                WHERE em.source_type = 'conversation' AND em.source_id = c.id
            )
              AND c.message_count >= %s
            """,
            (min_messages,),
            0,
        )
        or 0
    )


def entity_types(conn) -> list[str]:
    return [r["entity_type"] for r in rows(
        conn, "SELECT DISTINCT entity_type FROM entities ORDER BY entity_type"
    )]


def entity_projects(conn) -> list[str]:
    return [r["project_name"] for r in rows(
        conn,
        "SELECT DISTINCT project_name FROM entities "
        "WHERE project_name IS NOT NULL ORDER BY project_name",
    )]


def find_entity_ids(conn, terms: list[str], match_all: bool = False) -> list[int]:
    """Ids of entities whose name matches every (or any) term."""
    if not terms:
        return []
    joiner = " AND " if match_all else " OR "
    clause = "(" + joiner.join(["name ILIKE %s"] * len(terms)) + ")"
    found = rows(conn, f"SELECT id FROM entities WHERE {clause}", [f"%{t}%" for t in terms])
    return [int(r["id"]) for r in found]


def neighbour_ids(conn, entity_ids: list[int]) -> list[int]:
    """Ids one hop away from *entity_ids*, in either direction."""
    if not entity_ids:
        return []
    found = rows(
        conn,
        """
        SELECT to_entity AS id FROM relationships WHERE from_entity = ANY(%s)
        UNION
        SELECT from_entity AS id FROM relationships WHERE to_entity = ANY(%s)
        """,
        (entity_ids, entity_ids),
    )
    return [int(r["id"]) for r in found if r["id"] is not None]


def list_entities(
    conn,
    min_mentions: int = 1,
    entity_type: str | None = None,
    project: str | None = None,
    ids: list[int] | None = None,
    limit: int = 60,
) -> list[Row]:
    clauses = ["mention_count >= %s"]
    params: list[Any] = [min_mentions]
    if entity_type and entity_type != "All":
        clauses.append("entity_type = %s")
        params.append(entity_type)
    if project and project != "All":
        clauses.append("project_name = %s")
        params.append(project)
    if ids is not None:
        if not ids:
            return []
        clauses.append("id = ANY(%s)")
        params.append(ids)

    return rows(
        conn,
        f"""
        SELECT id, name, entity_type, project_name, mention_count, confidence, attributes,
               COALESCE(last_seen, first_seen) AS sort_date
        FROM entities
        WHERE {" AND ".join(clauses)}
        ORDER BY sort_date DESC NULLS LAST, mention_count DESC
        LIMIT %s
        """,
        [*params, limit],
    )


def relationships_among(conn, entity_ids: list[int]) -> list[Row]:
    """Edges whose both endpoints are inside *entity_ids* — the induced subgraph."""
    if not entity_ids:
        return []
    return rows(
        conn,
        """
        SELECT r.from_entity, r.to_entity, r.relation_type, r.confidence
        FROM relationships r
        WHERE r.from_entity = ANY(%s) AND r.to_entity = ANY(%s)
        """,
        (entity_ids, entity_ids),
    )


def get_entity(conn, entity_id: int) -> Row | None:
    return one(
        conn,
        """
        SELECT id, entity_type, name, canonical_name, attributes,
               first_seen, last_seen, mention_count, project_name, confidence, metadata
        FROM entities
        WHERE id = %s
        """,
        (entity_id,),
    )


def entity_relations(conn, entity_id: int, limit: int = 200) -> list[Row]:
    """Both directions of an entity's edges, with the far end resolved."""
    return rows(
        conn,
        """
        SELECT 'out'::text AS direction, r.relation_type, r.confidence,
               e.id AS other_id, e.name AS other_name, e.entity_type AS other_type
        FROM relationships r JOIN entities e ON e.id = r.to_entity
        WHERE r.from_entity = %s
        UNION ALL
        SELECT 'in'::text, r.relation_type, r.confidence,
               e.id, e.name, e.entity_type
        FROM relationships r JOIN entities e ON e.id = r.from_entity
        WHERE r.to_entity = %s
        ORDER BY confidence DESC NULLS LAST
        LIMIT %s
        """,
        (entity_id, entity_id, limit),
    )


def subgraph_for_sources(
    conn,
    sources: list[tuple[str, int]],
    limit_nodes: int = 120,
) -> dict[str, list[Row]]:
    """Entities mentioned by a specific set of records, plus induced edges.

    The Streamlit Knowledge Graph page rendered the *whole* graph and then let
    you filter it down. At any real size that is both unreadable and slow, and
    it answers a question nobody asked. Here the graph is always derived from
    what is currently on screen: these results, the entities they mention, and
    the edges between those entities.

    `sources` is a list of (source_type, source_id) pairs, matching
    `entity_mentions`. Passing an empty list returns an empty graph rather
    than silently falling back to everything.
    """
    if not sources:
        return {"nodes": [], "edges": []}

    types = [s[0] for s in sources]
    ids = [int(s[1]) for s in sources]

    nodes = rows(
        conn,
        """
        SELECT e.id, e.name, e.entity_type, e.project_name, e.mention_count,
               e.confidence::float AS confidence,
               count(*) AS hits_in_results
        FROM entity_mentions em
        JOIN entities e ON e.id = em.entity_id
        JOIN unnest(%s::text[], %s::bigint[]) AS s(stype, sid)
          ON s.stype = em.source_type AND s.sid = em.source_id
        GROUP BY e.id, e.name, e.entity_type, e.project_name, e.mention_count, e.confidence
        ORDER BY hits_in_results DESC, e.mention_count DESC
        LIMIT %s
        """,
        (types, ids, limit_nodes),
    )
    node_ids = [int(n["id"]) for n in nodes]
    return {"nodes": nodes, "edges": relationships_among(conn, node_ids)}
