#!/usr/bin/env python3
"""
Knowledge Graph Query CLI.

Commands:
  neighbors <entity>          Alle direkten Verbindungen einer Entity
  path <from> <to>            Shortest path zwischen zwei Entities
  timeline <entity>           Chronologischer Verlauf einer Entity
  top-entities [--type TYPE]  Top nach mention_count
  contradictions              Findet widersprüchliche Beziehungen
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from typing import Any

import psycopg2
import psycopg2.extras

DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "claude_memory"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}


def _connect() -> "psycopg2.extensions.connection":
    """Connect to PostgreSQL with a friendly error if the DB is unreachable."""
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}.\n"
            f"  Is it running? Try: docker compose up -d\n"
            f"  Or: brew services start postgresql@16\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e


def canonicalize(name: str) -> str:
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    no_accent = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r"\s+", " ", no_accent).strip().lower()


def resolve_entity(cursor: Any, query: str) -> list[dict[str, Any]]:
    """Sucht Entity nach exaktem canonical oder LIKE-Fallback. Gibt list[(id, name, type, project)] zurück."""
    canon = canonicalize(query)
    cursor.execute("""
        SELECT id, name, entity_type, project_name, mention_count
        FROM entities
        WHERE canonical_name = %s
        ORDER BY mention_count DESC
    """, (canon,))
    rows = cursor.fetchall()
    if rows:
        return rows
    # Fallback: LIKE
    cursor.execute("""
        SELECT id, name, entity_type, project_name, mention_count
        FROM entities
        WHERE canonical_name ILIKE %s
        ORDER BY mention_count DESC
        LIMIT 10
    """, (f"%{canon}%",))
    return cursor.fetchall()


def cmd_neighbors(cursor: Any, entity_query: str) -> None:
    rows = resolve_entity(cursor, entity_query)
    if not rows:
        print(f"Keine Entity gefunden für: {entity_query}")
        return
    if len(rows) > 1 and not all(r[0] == rows[0][0] for r in rows):
        print(f"Mehrere Treffer — nutze ersten ({rows[0][1]}):")
        for r in rows[:5]:
            print(f"  #{r[0]} {r[1]} [{r[2]}] (Mentions: {r[4]})")

    entity_id = rows[0][0]
    entity_name = rows[0][1]
    entity_type = rows[0][2]
    print(f"\n── Neighbors of {entity_name} [{entity_type}] (#{entity_id}) ──\n")

    # Outgoing
    cursor.execute("""
        SELECT r.relation_type, e.name, e.entity_type, e.id, r.confidence, r.source_id
        FROM relationships r
        JOIN entities e ON e.id = r.to_entity
        WHERE r.from_entity = %s
        ORDER BY r.confidence DESC
    """, (entity_id,))
    outgoing = cursor.fetchall()
    if outgoing:
        print("  Outgoing:")
        for r in outgoing:
            print(f"    → [{r[0]}] {r[1]} [{r[2]}] (#{r[3]}, conf={r[4]}, src_conv={r[5]})")

    # Incoming
    cursor.execute("""
        SELECT r.relation_type, e.name, e.entity_type, e.id, r.confidence, r.source_id
        FROM relationships r
        JOIN entities e ON e.id = r.from_entity
        WHERE r.to_entity = %s
        ORDER BY r.confidence DESC
    """, (entity_id,))
    incoming = cursor.fetchall()
    if incoming:
        print("\n  Incoming:")
        for r in incoming:
            print(f"    ← [{r[0]}] {r[1]} [{r[2]}] (#{r[3]}, conf={r[4]}, src_conv={r[5]})")

    if not outgoing and not incoming:
        print("  (keine Verbindungen)")


def cmd_path(cursor: Any, from_query: str, to_query: str) -> None:
    from_rows = resolve_entity(cursor, from_query)
    to_rows = resolve_entity(cursor, to_query)
    if not from_rows:
        print(f"Entity nicht gefunden: {from_query}")
        return
    if not to_rows:
        print(f"Entity nicht gefunden: {to_query}")
        return
    from_id = from_rows[0][0]
    to_id = to_rows[0][0]
    print(f"\n── Shortest Path: {from_rows[0][1]} (#{from_id}) → {to_rows[0][1]} (#{to_id}) ──\n")

    # BFS via recursive CTE. Nutzt ungerichteten Graphen.
    cursor.execute("""
        WITH RECURSIVE bfs AS (
            SELECT
                %s::bigint AS current_id,
                ARRAY[%s::bigint] AS path,
                ARRAY[]::text[] AS edge_labels,
                0 AS depth
            UNION ALL
            SELECT
                CASE WHEN r.from_entity = b.current_id THEN r.to_entity ELSE r.from_entity END,
                b.path || CASE WHEN r.from_entity = b.current_id THEN r.to_entity ELSE r.from_entity END,
                b.edge_labels || r.relation_type,
                b.depth + 1
            FROM bfs b
            JOIN relationships r ON r.from_entity = b.current_id OR r.to_entity = b.current_id
            WHERE b.depth < 6
              AND NOT (CASE WHEN r.from_entity = b.current_id THEN r.to_entity ELSE r.from_entity END = ANY(b.path))
        )
        SELECT path, edge_labels, depth FROM bfs
        WHERE current_id = %s
        ORDER BY depth ASC
        LIMIT 1
    """, (from_id, from_id, to_id))
    result = cursor.fetchone()
    if not result:
        print("  Kein Pfad gefunden (max depth 6).")
        return
    path_ids, edge_labels, depth = result
    # Lade Namen
    cursor.execute("SELECT id, name, entity_type FROM entities WHERE id = ANY(%s)", (path_ids,))
    id_to_meta = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

    print(f"  Länge: {depth} Hops")
    print()
    for i, eid in enumerate(path_ids):
        name, etype = id_to_meta.get(eid, ("?", "?"))
        print(f"    {name} [{etype}] (#{eid})")
        if i < len(edge_labels):
            print(f"      ↓ {edge_labels[i]}")


def cmd_timeline(cursor: Any, entity_query: str) -> None:
    rows = resolve_entity(cursor, entity_query)
    if not rows:
        print(f"Keine Entity gefunden für: {entity_query}")
        return
    entity_id = rows[0][0]
    entity_name = rows[0][1]
    print(f"\n── Timeline of {entity_name} (#{entity_id}) ──\n")

    cursor.execute("""
        SELECT em.created_at, em.source_type, em.source_id, em.context_snippet,
               c.summary, c.project_name
        FROM entity_mentions em
        LEFT JOIN conversations c ON c.id = em.source_id AND em.source_type = 'conversation'
        WHERE em.entity_id = %s
        ORDER BY em.created_at ASC
    """, (entity_id,))
    mentions = cursor.fetchall()
    for m in mentions:
        created_at, src_type, src_id, snippet, conv_title, proj = m
        print(f"  [{str(created_at)[:16]}] {src_type}#{src_id} ({proj or '–'})")
        if conv_title:
            print(f"    Titel: {conv_title}")
        if snippet:
            snippet_short = snippet[:200] + "..." if len(snippet) > 200 else snippet
            print(f"    »{snippet_short}«")
        print()

    # Relationship-Änderungen
    cursor.execute("""
        SELECT r.created_at, r.relation_type, e_from.name, e_to.name, r.source_id
        FROM relationships r
        JOIN entities e_from ON e_from.id = r.from_entity
        JOIN entities e_to ON e_to.id = r.to_entity
        WHERE r.from_entity = %s OR r.to_entity = %s
        ORDER BY r.created_at ASC
    """, (entity_id, entity_id))
    rels = cursor.fetchall()
    if rels:
        print("  Relations-Historie:")
        for r in rels:
            print(f"    [{str(r[0])[:16]}] {r[2]} --[{r[1]}]--> {r[3]} (src_conv={r[4]})")


def cmd_top(cursor: Any, entity_type: str | None, limit: int = 20) -> None:
    if entity_type:
        cursor.execute("""
            SELECT id, name, entity_type, project_name, mention_count
            FROM entities
            WHERE entity_type = %s
            ORDER BY mention_count DESC
            LIMIT %s
        """, (entity_type, limit))
    else:
        cursor.execute("""
            SELECT id, name, entity_type, project_name, mention_count
            FROM entities
            ORDER BY mention_count DESC
            LIMIT %s
        """, (limit,))
    rows = cursor.fetchall()
    print(f"\n── Top {len(rows)} Entities" + (f" ({entity_type})" if entity_type else "") + " ──\n")
    print(f"  {'ID':>5}  {'Type':<14} {'Mentions':>8}  {'Name':<40} Project")
    print(f"  {'─'*5}  {'─'*14} {'─'*8}  {'─'*40} {'─'*15}")
    for r in rows:
        print(f"  #{r[0]:<4} {r[2]:<14} {r[4]:>8}  {r[1][:40]:<40} {r[3] or '–'}")


def cmd_contradictions(cursor: Any) -> None:
    """Findet Relationships mit gleichem from+to+type aber widersprüchlichen Attributes oder unterschiedlichen Confidence-Werten."""
    print("\n── Widersprüchliche Beziehungen ──\n")

    # 1) Gleiche from+to aber unterschiedliche Relation-Types
    cursor.execute("""
        SELECT e_from.name, e_to.name,
               array_agg(DISTINCT r.relation_type ORDER BY r.relation_type) AS types,
               count(*) AS n
        FROM relationships r
        JOIN entities e_from ON e_from.id = r.from_entity
        JOIN entities e_to ON e_to.id = r.to_entity
        GROUP BY r.from_entity, r.to_entity, e_from.name, e_to.name
        HAVING count(DISTINCT r.relation_type) > 1
        ORDER BY n DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    if rows:
        print("  Verschiedene Relation-Types zwischen gleichen Entities:")
        for r in rows:
            print(f"    {r[0]} → {r[1]}: {r[2]} ({r[3]}x)")
        print()

    # 2) Gleiche Relation aber unterschiedliche Attributes
    cursor.execute("""
        SELECT e_from.name, e_to.name, r.relation_type,
               array_agg(DISTINCT r.attributes::text) AS attrs,
               count(*) AS n
        FROM relationships r
        JOIN entities e_from ON e_from.id = r.from_entity
        JOIN entities e_to ON e_to.id = r.to_entity
        WHERE r.attributes IS NOT NULL AND r.attributes::text != '{}'
        GROUP BY r.from_entity, r.to_entity, r.relation_type, e_from.name, e_to.name
        HAVING count(DISTINCT r.attributes::text) > 1
        LIMIT 20
    """)
    rows = cursor.fetchall()
    if rows:
        print("  Gleiche Relation, unterschiedliche Attributes:")
        for r in rows:
            print(f"    {r[0]} --[{r[2]}]--> {r[1]}")
            for a in r[3]:
                print(f"      attrs: {a}")
        print()

    # 3) Entities mit mehreren widersprüchlichen Attributes über Mentions
    cursor.execute("""
        SELECT name, entity_type,
               jsonb_object_keys(attributes) AS attr_key,
               count(*) OVER (PARTITION BY id) AS mentions
        FROM entities
        WHERE attributes != '{}'::jsonb
        ORDER BY mentions DESC
        LIMIT 20
    """)
    # nicht extrem aussagekräftig, skip falls leer
    print("  (Siehe oben für widersprüchliche Relationships.)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Knowledge Graph Query CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_n = sub.add_parser("neighbors")
    p_n.add_argument("entity")

    p_p = sub.add_parser("path")
    p_p.add_argument("from_entity")
    p_p.add_argument("to_entity")

    p_t = sub.add_parser("timeline")
    p_t.add_argument("entity")

    p_top = sub.add_parser("top-entities")
    p_top.add_argument("--type", default=None, choices=["person", "project", "technology", "decision", "concept", "organization"])
    p_top.add_argument("--limit", type=int, default=20)

    sub.add_parser("contradictions")

    args = ap.parse_args()

    conn = _connect()
    cursor = conn.cursor()

    try:
        if args.cmd == "neighbors":
            cmd_neighbors(cursor, args.entity)
        elif args.cmd == "path":
            cmd_path(cursor, args.from_entity, args.to_entity)
        elif args.cmd == "timeline":
            cmd_timeline(cursor, args.entity)
        elif args.cmd == "top-entities":
            cmd_top(cursor, args.type, args.limit)
        elif args.cmd == "contradictions":
            cmd_contradictions(cursor)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
