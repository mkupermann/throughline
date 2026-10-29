"""Curation queues and the mutations that drain them — the ``/curate`` surface.

Why forget is not a delete
--------------------------
``scripts/forget.py::forget_chunks`` issues ``DELETE FROM memory_chunks``. That
is unrecoverable, which makes an "undo" affordance a lie: by the time the toast
appears there is nothing left to restore.

For a tool whose entire purpose is long-term memory, a one-click irreversible
delete is also the wrong default. So forgetting is two-tier:

* :func:`forget` sets ``status = 'forgotten'``. The row survives, drops out of
  every retrieval path, and can be restored — immediately via the undo toast,
  or at any later point from the Forgotten queue.
* **Purge** (``scripts/forget.py``, surfaced under Operate) is the real delete.
  It stays, because soft-deleting a chunk that contains a leaked credential
  does not remove the credential from the database. Purge is explicit, bulk,
  and clearly labelled as unrecoverable.

``memory_chunks.status`` is a plain ``text`` column, not an enum, so adding
``'forgotten'`` required no migration.

Undo durability
---------------
The 5-second undo token lives in process memory (see ``api/undo.py``): it is a
UI affordance for an immediate mis-click, not an audit trail. What makes that
acceptable is that the underlying mutation is *already* reversible and durable
— losing the token to a server restart costs you a trip to the Forgotten
queue, not your data. ``memory_reflections`` records every mutation
permanently either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from ._exec import Row, one, rows, scalar

#: Status marking a soft-deleted chunk. Excluded from every retrieval path.
FORGOTTEN = "forgotten"

#: Statuses that never appear in search results.
HIDDEN_STATUSES: tuple[str, ...] = (FORGOTTEN,)

QueueName = Literal[
    "contradictions",
    "drift",
    "superseded",
    "low-confidence",
    "missing-embeddings",
    "expiring",
    "never-accessed",
    "forgotten",
]

LOW_CONFIDENCE_DEFAULT = 0.6
NEVER_ACCESSED_DAYS = 30


@dataclass
class Queue:
    name: str
    title: str
    description: str
    count: int
    severity: str
    actions: list[str]


# ── Queue contents ──────────────────────────────────────────────────────────


def _chunk_projection(alias: str = "mc") -> str:
    return f"""
        {alias}.id,
        {alias}.category::text            AS category,
        left({alias}.content, 400)        AS content,
        {alias}.confidence::float         AS confidence,
        {alias}.project_name,
        {alias}.created_at,
        COALESCE({alias}.status,'active') AS status,
        {alias}.access_count,
        {alias}.expires_at
    """


def queue_low_confidence(conn, threshold: float = LOW_CONFIDENCE_DEFAULT, limit: int = 200) -> list[Row]:
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()}
        FROM memory_chunks mc
        WHERE COALESCE(mc.status,'active') = 'active' AND mc.confidence < %s
        ORDER BY mc.confidence ASC, mc.created_at DESC
        LIMIT %s
        """,
        (threshold, limit),
    )


def queue_missing_embeddings(conn, model: str | None = None, limit: int = 200) -> list[Row]:
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()}
        FROM memory_chunks mc
        WHERE COALESCE(mc.status,'active') = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM embeddings e
              WHERE e.source_type = 'memory_chunk' AND e.source_id = mc.id
                AND (%s::text IS NULL OR e.model = %s)
          )
        ORDER BY mc.created_at DESC
        LIMIT %s
        """,
        (model, model, limit),
    )


def queue_expiring(conn, limit: int = 200) -> list[Row]:
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()}
        FROM memory_chunks mc
        WHERE mc.expires_at IS NOT NULL AND COALESCE(mc.status,'active') = 'active'
        ORDER BY mc.expires_at ASC
        LIMIT %s
        """,
        (limit,),
    )


def queue_never_accessed(conn, older_than_days: int = NEVER_ACCESSED_DAYS, limit: int = 200) -> list[Row]:
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()}
        FROM memory_chunks mc
        WHERE COALESCE(mc.access_count, 0) = 0
          AND COALESCE(mc.status,'active') = 'active'
          AND mc.created_at < now() - make_interval(days => %s)
        ORDER BY mc.created_at ASC
        LIMIT %s
        """,
        (older_than_days, limit),
    )


def queue_superseded(conn, limit: int = 200) -> list[Row]:
    """Chunks that were replaced, with the chunk that replaced them resolved."""
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()},
               mc.superseded_by,
               left(sb.content, 200) AS superseded_by_content
        FROM memory_chunks mc
        LEFT JOIN memory_chunks sb ON sb.id = mc.superseded_by
        WHERE mc.status IN ('superseded','merged')
           OR (mc.merged_from IS NOT NULL AND array_length(mc.merged_from,1) > 0)
        ORDER BY mc.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def queue_forgotten(conn, limit: int = 200) -> list[Row]:
    """Soft-deleted chunks — the safety net behind the 5-second undo."""
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()}
        FROM memory_chunks mc
        WHERE mc.status = %s
        ORDER BY mc.created_at DESC
        LIMIT %s
        """,
        (FORGOTTEN, limit),
    )


def latest_audit(conn) -> Row | None:
    """Return a UI-safe summary of the latest extraction audit."""
    latest = one(
        conn,
        "SELECT id, affected_chunks, action_taken, reasoning, created_at "
        "FROM memory_reflections "
        "WHERE reflection_type = 'audit' ORDER BY created_at DESC NULLS LAST LIMIT 1",
    )
    if not latest:
        return None
    reasoning = str(latest.get("reasoning") or "")
    sampled_match = re.search(r"Sampled\s+(\d+)\s+chunks?\b", reasoning, re.IGNORECASE)
    drifted_match = re.search(r"(\d+)\s+drift(?:ed)?\b", reasoning, re.IGNORECASE)
    affected = list(latest.get("affected_chunks") or [])
    sampled = int(sampled_match.group(1)) if sampled_match else len(affected)
    drifted = int(drifted_match.group(1)) if drifted_match else len(affected)
    action = str(latest.get("action_taken") or "")
    state = "no-samples" if action == "no_samples_v2" or sampled == 0 else "findings" if drifted else "clear"
    return {
        "id": int(latest["id"]),
        "created_at": latest.get("created_at"),
        "sampled": sampled,
        "drifted": drifted,
        "state": state,
        "finding_ids": affected if action.endswith("_v2") else [],
        "findings_available": action.endswith("_v2"),
    }


def queue_drift(conn, limit: int = 200) -> list[Row]:
    """Chunks flagged by the most recent extraction drift audit.

    The audit writes a reflection per run with the affected chunk ids, so the
    queue is those ids resolved back to their chunks. Nothing to show before
    the first audit has run — which is correct, not an empty-state bug.
    """
    latest = latest_audit(conn)
    if not latest or not latest.get("findings_available") or not latest.get("finding_ids"):
        return []
    return rows(
        conn,
        f"""
        SELECT {_chunk_projection()}
        FROM memory_chunks mc
        WHERE mc.id = ANY(%s) AND COALESCE(mc.status,'active') = 'active'
        ORDER BY mc.created_at DESC
        LIMIT %s
        """,
        (list(latest["finding_ids"]), limit),
    )


def drift_count(conn, audit: Row | None = None) -> int:
    """Count active findings from the latest reviewable audit."""
    latest = audit if audit is not None else latest_audit(conn)
    if not latest or not latest.get("findings_available") or not latest.get("finding_ids"):
        return 0
    return int(
        scalar(
            conn,
            """
            SELECT count(*)
            FROM memory_chunks mc
            WHERE mc.id = ANY(%s)
              AND COALESCE(mc.status, 'active') = 'active'
            """,
            (list(latest["finding_ids"]),),
            0,
        )
        or 0
    )


def queue_contradictions(conn, limit: int = 200) -> list[Row]:
    """Outstanding contradiction pairs.

    Reads the recorded conflict reflections rather than re-running detection:
    the semantic strategy is an all-pairs comparison and must not run inside a
    request. `throughline reflect` / the Operate surface produce these.
    """
    return rows(
        conn,
        """
        SELECT r.id,
               r.reflection_type,
               r.action_taken,
               r.reasoning,
               r.confidence::float AS confidence,
               r.created_at,
               r.affected_chunks
        FROM memory_reflections r
        WHERE r.reflection_type IN ('contradiction', 'conflict')
          AND COALESCE(r.action_taken, '') NOT IN ('resolved', 'dismissed')
        ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


QUEUE_FUNCS = {
    "contradictions": queue_contradictions,
    "drift": queue_drift,
    "superseded": queue_superseded,
    "low-confidence": queue_low_confidence,
    "missing-embeddings": queue_missing_embeddings,
    "expiring": queue_expiring,
    "never-accessed": queue_never_accessed,
    "forgotten": queue_forgotten,
}

QUEUE_META: dict[str, dict[str, Any]] = {
    "contradictions": {
        "title": "Contradictions",
        "description": "Memory that disagrees with itself across tools.",
        "severity": "warning",
        "actions": ["dismiss"],
    },
    "drift": {
        "title": "Drift audit hits",
        "description": "Chunks the last audit found no longer matching their source.",
        "severity": "warning",
        "actions": ["forget"],
    },
    "superseded": {
        "title": "Superseded chains",
        "description": "Chunks replaced or merged by a newer one.",
        "severity": "info",
        "actions": ["forget"],
    },
    "low-confidence": {
        "title": "Low confidence",
        "description": "Stored below the confidence threshold and worth a look.",
        "severity": "info",
        "actions": ["forget", "raise_confidence"],
    },
    "missing-embeddings": {
        "title": "Missing embeddings",
        "description": "Active chunks that semantic search cannot reach.",
        "severity": "info",
        "actions": [],
    },
    "expiring": {
        "title": "Expiring",
        "description": "Chunks with an expiry date set.",
        "severity": "info",
        "actions": ["forget", "clear_expiry"],
    },
    "never-accessed": {
        "title": "Never accessed",
        "description": "Older chunks nothing has ever read.",
        "severity": "info",
        "actions": ["forget"],
    },
    "forgotten": {
        "title": "Forgotten",
        "description": "Soft-deleted. Restorable here; purge under Operate to delete for good.",
        "severity": "info",
        "actions": ["restore"],
    },
}


def queue_counts(conn, low_confidence: float = LOW_CONFIDENCE_DEFAULT) -> dict[str, int]:
    """Counts for every queue in one round trip.

    One query rather than eight: the badges all render together, and eight
    sequential counts on a cold connection is the kind of thing that makes a
    page feel slow for no reason.
    """
    row = (
        one(
            conn,
            """
        SELECT
          (SELECT count(*) FROM memory_reflections r
            WHERE r.reflection_type IN ('contradiction','conflict')
              AND COALESCE(r.action_taken,'') NOT IN ('resolved','dismissed'))      AS contradictions,
          (SELECT count(*) FROM memory_chunks mc
            WHERE COALESCE(mc.status,'active')='active' AND mc.confidence < %s)     AS low_confidence,
          (SELECT count(*) FROM memory_chunks mc
            WHERE COALESCE(mc.status,'active')='active'
              AND NOT EXISTS (SELECT 1 FROM embeddings e
                              WHERE e.source_type='memory_chunk' AND e.source_id=mc.id)) AS missing_embeddings,
          (SELECT count(*) FROM memory_chunks mc
            WHERE mc.expires_at IS NOT NULL
              AND COALESCE(mc.status,'active')='active')                            AS expiring,
          (SELECT count(*) FROM memory_chunks mc
            WHERE COALESCE(mc.access_count,0)=0 AND COALESCE(mc.status,'active')='active'
              AND mc.created_at < now() - make_interval(days => %s))                AS never_accessed,
          (SELECT count(*) FROM memory_chunks mc
            WHERE mc.status IN ('superseded','merged')
               OR (mc.merged_from IS NOT NULL AND array_length(mc.merged_from,1) > 0)) AS superseded,
          (SELECT count(*) FROM memory_chunks mc WHERE mc.status = %s)              AS forgotten
        """,
            (low_confidence, NEVER_ACCESSED_DAYS, FORGOTTEN),
        )
        or {}
    )

    drift = len(queue_drift(conn, limit=10_000))

    return {
        "contradictions": int(row.get("contradictions") or 0),
        "drift": drift,
        "superseded": int(row.get("superseded") or 0),
        "low-confidence": int(row.get("low_confidence") or 0),
        "missing-embeddings": int(row.get("missing_embeddings") or 0),
        "expiring": int(row.get("expiring") or 0),
        "never-accessed": int(row.get("never_accessed") or 0),
        "forgotten": int(row.get("forgotten") or 0),
    }


# ── Reversible mutations ────────────────────────────────────────────────────
# Each returns an "inverse" describing exactly how to undo it, so the undo
# path never has to re-derive intent from a diff.


def _log(conn, action: str, ids: list[int], reason: str) -> int | None:
    return scalar(
        conn,
        """
        INSERT INTO memory_reflections
            (reflection_type, affected_chunks, action_taken, reasoning, confidence)
        VALUES ('curate', %s, %s, %s, 1.0)
        RETURNING id
        """,
        (ids, action, reason[:4000]),
    )


def forget(conn, ids: list[int], reason: str = "forgotten from Curate") -> dict[str, Any]:
    """Soft-delete. Records each chunk's prior status so undo is exact."""
    if not ids:
        return {"changed": 0, "inverse": None}

    prior = rows(
        conn,
        "SELECT id, COALESCE(status,'active') AS status FROM memory_chunks "
        "WHERE id = ANY(%s) AND COALESCE(status,'active') <> %s",
        (ids, FORGOTTEN),
    )
    if not prior:
        return {"changed": 0, "inverse": None}

    target = [int(r["id"]) for r in prior]
    changed = scalar(
        conn,
        "WITH u AS (UPDATE memory_chunks SET status = %s WHERE id = ANY(%s) RETURNING 1) SELECT count(*) FROM u",
        (FORGOTTEN, target),
    )
    _log(conn, "forget", target, reason)
    conn.commit()
    return {
        "changed": int(changed or 0),
        "inverse": {"op": "restore_status", "states": {str(r["id"]): r["status"] for r in prior}},
    }


def restore(conn, states: dict[str, str], reason: str = "restored") -> dict[str, Any]:
    """Put chunks back to an exact prior status. The inverse of :func:`forget`."""
    if not states:
        return {"changed": 0, "inverse": None}
    ids = [int(k) for k in states]
    changed = 0
    for chunk_id, status in states.items():
        changed += (
            scalar(
                conn,
                "WITH u AS (UPDATE memory_chunks SET status = %s WHERE id = %s RETURNING 1) SELECT count(*) FROM u",
                (status, int(chunk_id)),
            )
            or 0
        )
    _log(conn, "restore", ids, reason)
    conn.commit()
    return {
        "changed": int(changed),
        "inverse": {"op": "forget", "ids": ids},
    }


def set_confidence(conn, ids: list[int], value: float, reason: str = "confidence adjusted") -> dict[str, Any]:
    if not ids:
        return {"changed": 0, "inverse": None}
    prior = rows(
        conn,
        "SELECT id, confidence::float AS confidence FROM memory_chunks WHERE id = ANY(%s)",
        (ids,),
    )
    changed = scalar(
        conn,
        "WITH u AS (UPDATE memory_chunks SET confidence = %s WHERE id = ANY(%s) RETURNING 1) SELECT count(*) FROM u",
        (value, ids),
    )
    _log(conn, "set_confidence", ids, reason)
    conn.commit()
    return {
        "changed": int(changed or 0),
        "inverse": {
            "op": "restore_confidence",
            "values": {str(r["id"]): r["confidence"] for r in prior},
        },
    }


def restore_confidence(conn, values: dict[str, float], reason: str = "confidence restored") -> dict[str, Any]:
    if not values:
        return {"changed": 0, "inverse": None}
    changed = 0
    for chunk_id, conf in values.items():
        changed += (
            scalar(
                conn,
                "WITH u AS (UPDATE memory_chunks SET confidence = %s WHERE id = %s RETURNING 1) SELECT count(*) FROM u",
                (conf, int(chunk_id)),
            )
            or 0
        )
    _log(conn, "restore_confidence", [int(k) for k in values], reason)
    conn.commit()
    return {"changed": int(changed), "inverse": None}


def clear_expiry(conn, ids: list[int], reason: str = "expiry cleared") -> dict[str, Any]:
    if not ids:
        return {"changed": 0, "inverse": None}
    prior = rows(
        conn,
        "SELECT id, expires_at FROM memory_chunks WHERE id = ANY(%s) AND expires_at IS NOT NULL",
        (ids,),
    )
    if not prior:
        return {"changed": 0, "inverse": None}
    target = [int(r["id"]) for r in prior]
    changed = scalar(
        conn,
        "WITH u AS (UPDATE memory_chunks SET expires_at = NULL WHERE id = ANY(%s) RETURNING 1) SELECT count(*) FROM u",
        (target,),
    )
    _log(conn, "clear_expiry", target, reason)
    conn.commit()
    return {
        "changed": int(changed or 0),
        "inverse": {
            "op": "restore_expiry",
            "values": {str(r["id"]): r["expires_at"].isoformat() for r in prior},
        },
    }


def restore_expiry(conn, values: dict[str, str], reason: str = "expiry restored") -> dict[str, Any]:
    if not values:
        return {"changed": 0, "inverse": None}
    changed = 0
    for chunk_id, when in values.items():
        changed += (
            scalar(
                conn,
                "WITH u AS (UPDATE memory_chunks SET expires_at = %s WHERE id = %s RETURNING 1) SELECT count(*) FROM u",
                (when, int(chunk_id)),
            )
            or 0
        )
    _log(conn, "restore_expiry", [int(k) for k in values], reason)
    conn.commit()
    return {"changed": int(changed), "inverse": None}


def dismiss_reflections(conn, ids: list[int], reason: str = "dismissed") -> dict[str, Any]:
    """Mark contradiction reflections handled without touching the chunks."""
    if not ids:
        return {"changed": 0, "inverse": None}
    prior = rows(
        conn,
        "SELECT id, action_taken FROM memory_reflections WHERE id = ANY(%s)",
        (ids,),
    )
    changed = scalar(
        conn,
        "WITH u AS (UPDATE memory_reflections SET action_taken = 'dismissed' "
        "WHERE id = ANY(%s) RETURNING 1) SELECT count(*) FROM u",
        (ids,),
    )
    conn.commit()
    return {
        "changed": int(changed or 0),
        "inverse": {
            "op": "restore_action",
            "values": {str(r["id"]): r["action_taken"] for r in prior},
        },
    }


def restore_action(conn, values: dict[str, Any], reason: str = "restored") -> dict[str, Any]:
    if not values:
        return {"changed": 0, "inverse": None}
    changed = 0
    for rid, action in values.items():
        changed += (
            scalar(
                conn,
                "WITH u AS (UPDATE memory_reflections SET action_taken = %s WHERE id = %s RETURNING 1) "
                "SELECT count(*) FROM u",
                (action, int(rid)),
            )
            or 0
        )
    conn.commit()
    return {"changed": int(changed), "inverse": None}


#: op name -> (function, payload key). The undo path is data-driven so a new
#: reversible action cannot forget to register its inverse.
INVERSE_OPS = {
    "restore_status": (restore, "states"),
    "forget": (forget, "ids"),
    "restore_confidence": (restore_confidence, "values"),
    "restore_expiry": (restore_expiry, "values"),
    "restore_action": (restore_action, "values"),
}
