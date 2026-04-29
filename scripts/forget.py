"""Cascade-delete primitives for memory chunks and entities.

Both functions run in a single transaction and write an audit row to
`memory_reflections`. The caller owns the connection but does NOT commit —
these functions commit on success and rollback on failure.

Convention for `memory_reflections.reflection_type`:
  - 'forget'         — chunk(s) deleted via forget_chunks
  - 'forget_entity'  — entity deleted via forget_entity (FK cascades take care
                       of entity_mentions and relationships)
"""
from __future__ import annotations

from typing import Iterable


def forget_chunks(conn, ids: Iterable[int], *, reason: str) -> dict:
    id_list = [int(i) for i in ids]
    if not id_list:
        return {"chunks": 0, "embeddings": 0, "reflection_id": None}

    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM embeddings WHERE source_type = 'memory_chunk' AND source_id = ANY(%s)",
                (id_list,),
            )
            embeddings_deleted = cur.rowcount

            cur.execute(
                "UPDATE memory_chunks SET superseded_by = NULL WHERE superseded_by = ANY(%s)",
                (id_list,),
            )

            cur.execute(
                "DELETE FROM memory_chunks WHERE id = ANY(%s)",
                (id_list,),
            )
            chunks_deleted = cur.rowcount

            cur.execute(
                """
                INSERT INTO memory_reflections
                    (reflection_type, affected_chunks, action_taken, reasoning, confidence)
                VALUES ('forget', %s, 'deleted', %s, 1.0)
                RETURNING id
                """,
                (id_list, reason[:4000]),
            )
            reflection_id = cur.fetchone()[0]
        conn.commit()
        return {
            "chunks": chunks_deleted,
            "embeddings": embeddings_deleted,
            "reflection_id": reflection_id,
        }
    except Exception:
        conn.rollback()
        raise


def forget_entity(conn, entity_id: int, *, reason: str) -> dict:
    eid = int(entity_id)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM entity_mentions WHERE entity_id = %s", (eid,))
            mentions = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*) FROM relationships WHERE from_entity = %s OR to_entity = %s",
                (eid, eid),
            )
            relationships = int(cur.fetchone()[0])

            cur.execute("DELETE FROM entities WHERE id = %s", (eid,))
            entity_deleted = cur.rowcount

            cur.execute(
                """
                INSERT INTO memory_reflections
                    (reflection_type, affected_chunks, action_taken, reasoning, confidence)
                VALUES ('forget_entity', %s, 'deleted', %s, 1.0)
                RETURNING id
                """,
                ([eid], reason[:4000]),
            )
            reflection_id = cur.fetchone()[0]
        conn.commit()
        return {
            "entity": entity_deleted,
            "mentions": mentions,
            "relationships": relationships,
            "reflection_id": reflection_id,
        }
    except Exception:
        conn.rollback()
        raise
