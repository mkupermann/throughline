"""The /curate surface: queues that keep the memory base trustworthy."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from throughline import queries as Q
from throughline.queries.curate import INVERSE_OPS, QUEUE_FUNCS, QUEUE_META

from ..deps import connection
from ..settings import Settings
from ..undo import registry
from .common import get_settings

router = APIRouter(tags=["curate"])

Action = Literal["forget", "restore", "raise_confidence", "clear_expiry", "dismiss"]


class ActionRequest(BaseModel):
    action: Action
    ids: list[int] = Field(default_factory=list, max_length=5000)
    reason: str = "curated from the Curate surface"
    #: Only used by raise_confidence.
    value: float | None = Field(default=None, ge=0, le=1)


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in r.items()}


@router.get("/curate/queues")
def queues(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        counts = Q.curate.queue_counts(conn)
    return {
        "queues": [
            {
                "name": name,
                "count": counts.get(name, 0),
                **QUEUE_META[name],
            }
            for name in QUEUE_FUNCS
        ],
        "total": sum(counts.values()),
    }


@router.get("/curate/queue/{name}")
def queue(
    name: str,
    limit: int = Query(200, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    fn = QUEUE_FUNCS.get(name)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"Unknown queue {name!r}")
    with connection(settings) as conn:
        items = fn(conn, limit=limit)
    return {"name": name, **QUEUE_META[name], "items": [_row(r) for r in items]}


@router.post("/curate/act")
def act(
    body: ActionRequest = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Apply a bulk action and return an undo token.

    Replaying the same Idempotency-Key returns the first result untouched, so
    an impatient double-click cannot forget the same chunks twice and leave
    two competing inverses behind.
    """
    cached = registry.seen(idempotency_key)
    if cached is not None:
        return cached

    if not body.ids:
        return {"changed": 0, "undo_token": None, "message": "Nothing selected."}

    with connection(settings) as conn:
        if body.action == "forget":
            result = Q.curate.forget(conn, body.ids, reason=body.reason)
            label = f"Forgot {result['changed']} chunk(s)"
        elif body.action == "restore":
            states = {str(i): "active" for i in body.ids}
            result = Q.curate.restore(conn, states, reason=body.reason)
            label = f"Restored {result['changed']} chunk(s)"
        elif body.action == "raise_confidence":
            if body.value is None:
                raise HTTPException(status_code=422, detail="raise_confidence needs `value`.")
            result = Q.curate.set_confidence(conn, body.ids, body.value, reason=body.reason)
            label = f"Set confidence on {result['changed']} chunk(s)"
        elif body.action == "clear_expiry":
            result = Q.curate.clear_expiry(conn, body.ids, reason=body.reason)
            label = f"Cleared expiry on {result['changed']} chunk(s)"
        else:  # dismiss
            result = Q.curate.dismiss_reflections(conn, body.ids, reason=body.reason)
            label = f"Dismissed {result['changed']} item(s)"

    token = registry.register(result.get("inverse"), label)
    payload = {
        "changed": result["changed"],
        "undo_token": token,
        "message": label,
        # The ids the caller can drop from its local list without refetching.
        "affected_ids": body.ids,
    }
    registry.remember(idempotency_key, payload)
    return payload


class NewChunk(BaseModel):
    content: str = Field(..., min_length=1, max_length=20_000)
    category: str
    project_name: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=32)
    confidence: float = Field(default=0.8, ge=0, le=1)


@router.post("/curate/chunk")
def create_chunk(
    body: NewChunk = Body(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Record a memory chunk by hand.

    Most memory arrives through extraction, but a fact you want remembered
    right now — a contact, a decision made in a meeting — should not require
    dropping to SQL. The old GUI had this form; keeping it is parity, not
    scope creep.
    """
    try:
        with connection(settings) as conn:
            chunk_id = Q.memory.insert_chunk(
                conn,
                content=body.content,
                category=body.category,
                project_name=body.project_name or None,
                tags=body.tags,
                confidence=body.confidence,
            )
            conn.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Undoable like every other mutation here.
    token = registry.register({"op": "forget", "ids": [chunk_id]}, "Added 1 chunk")
    return {"id": chunk_id, "undo_token": token, "message": "Chunk added"}


@router.get("/curate/categories")
def categories() -> dict[str, Any]:
    return {"categories": list(Q.memory.CATEGORIES)}


@router.post("/curate/undo")
def undo(
    token: str = Body(..., embed=True),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    entry = registry.take(token)
    if entry is None:
        raise HTTPException(
            status_code=410,
            detail="That undo has expired or was already used. "
                   "Forgotten chunks can still be restored from the Forgotten queue.",
        )

    op = INVERSE_OPS.get(entry.op)
    if op is None:
        raise HTTPException(status_code=500, detail=f"No inverse registered for {entry.op!r}")

    fn, key = op
    with connection(settings) as conn:
        result = fn(conn, entry.payload.get(key) or {}, reason="undo")

    return {"changed": result["changed"], "message": f"Undid: {entry.label}"}
