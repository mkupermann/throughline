"""The /find surface: one query, every record type."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from throughline import embedding
from throughline import queries as Q
from throughline.queries.find import FindFilters

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["find"])

MAX_LIMIT = 200


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe projection. Datetimes become ISO strings here rather than
    relying on the encoder, so the shape is identical everywhere."""
    occurred = row.get("occurred_at")
    return {
        "kind": row["kind"],
        "id": int(row["id"]),
        "title": row.get("title"),
        "snippet": row.get("snippet"),
        "project": row.get("project"),
        "occurred_at": occurred.isoformat() if hasattr(occurred, "isoformat") else occurred,
        "category": row.get("category"),
        "status": row.get("status"),
        "confidence": row.get("confidence"),
        "conversation_id": row.get("conversation_id"),
        "score": row.get("score"),
        "retrievers": row.get("retrievers", 1),
    }


@router.get("/find")
def find(
    q: str = Query("", description="Search text."),
    kind: list[str] = Query(default=[]),
    category: list[str] = Query(default=[]),
    project: list[str] = Query(default=[]),
    provider: list[str] = Query(default=[]),
    status: list[str] = Query(default=[]),
    tag: list[str] = Query(default=[]),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    since: datetime | None = None,
    until: datetime | None = None,
    has_embedding: bool | None = None,
    semantic: bool = Query(True, description="Include the vector retriever when available."),
    limit: int = Query(30, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    filters = FindFilters(
        kinds=kind,
        categories=category,
        projects=project,
        providers=provider,
        statuses=status,
        tags=tag,
        min_confidence=min_confidence,
        since=since,
        until=until,
        has_embedding=has_embedding,
    )

    vector_literal = model = column = None
    backend = embedding.backend_info() if semantic else None
    if backend and backend.available:
        vec = embedding.embed_query(q) if q.strip() else None
        if vec is not None:
            vector_literal = embedding.vec_literal(vec)
            model, column = backend.model, backend.column

    has_filters = (
        any([kind, category, project, provider, status, tag, since, until])
        or min_confidence is not None
        or has_embedding is not None
    )

    with connection(settings) as conn:
        if not q.strip() and has_filters:
            # No search text, but something to narrow by: this is browsing,
            # not searching. Without it the Timeline and Graph views would be
            # empty exactly when they are most useful.
            result = Q.find.browse(conn, filters=filters, limit=limit, offset=offset)
        else:
            result = Q.find.find(
                conn,
                q,
                filters=filters,
                limit=limit,
                offset=offset,
                vector_literal=vector_literal,
                model=model,
                column=column,
            )

    notes = list(result.notes)
    if semantic and backend and not backend.available and q.strip():
        notes.append(backend.reason)

    return {
        "query": q,
        "items": [_serialise(r) for r in result.items],
        "total": result.total,
        "limit": limit,
        "offset": offset,
        "modes": result.modes,
        "notes": notes,
        "backend": {
            "available": bool(backend and backend.available),
            "label": backend.label if backend else "disabled",
        },
    }


class GraphRequest(BaseModel):
    sources: list[tuple[str, int]] = Field(default_factory=list, max_length=1000)
    limit_nodes: int = Field(default=120, ge=1, le=500)


@router.post("/find/graph")
def graph(
    body: GraphRequest = Body(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Entity subgraph induced by the records currently on screen."""
    with connection(settings) as conn:
        data = Q.entities.subgraph_for_sources(conn, [tuple(s) for s in body.sources], limit_nodes=body.limit_nodes)
    return {
        "nodes": [{k: _iso_any(v) for k, v in n.items()} for n in data["nodes"]],
        "edges": [{k: _iso_any(v) for k, v in e.items()} for e in data["edges"]],
    }


def _iso_any(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/find/facets")
def facets(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        data = Q.find.facets(conn)
    return {k: [{"value": str(i["value"]), "n": int(i["n"])} for i in v] for k, v in data.items()}


# ── Detail routes ───────────────────────────────────────────────────────────

DetailKind = Literal["conversation", "memory", "entity", "project", "skill", "prompt"]


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _jsonify(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: _iso(v) for k, v in row.items()}


@router.get("/detail/project/by-name/{name:path}")
def project_by_name(
    name: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Project detail keyed by name.

    A project is identified by its name everywhere else in the schema; the
    `projects` table is optional enrichment that lags behind reality. Keying
    detail on the registry id made every unregistered project unreachable.
    """
    from fastapi import HTTPException

    with connection(settings) as conn:
        record = Q.skills.get_project_by_name(conn, name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No project named {name!r}")
    return {"kind": "project", "record": _jsonify(record), "related": {}}


#: Messages returned per detail request. A long session runs to thousands —
#: this one is 3,398 — and its transcript is 18 MB on disk, so the whole thing
#: cannot come down in one response. The previous code took the first 500 and
#: said nothing, which reads as a conversation that stops mid-sentence. The cap
#: stays; what changes is that the response now carries the true total and an
#: offset, so the reader knows there is more and can ask for it.
MESSAGE_PAGE = 500


@router.get("/detail/{kind}/{item_id}")
def detail(
    kind: DetailKind,
    item_id: int,
    msg_offset: int = Query(0, ge=0, description="First message to return."),
    msg_limit: int = Query(MESSAGE_PAGE, ge=1, le=2000),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    from fastapi import HTTPException

    with connection(settings) as conn:
        if kind == "conversation":
            record = Q.conversations.get_conversation(conn, item_id)
            msgs = Q.conversations.messages_for(conn, item_id, limit=msg_limit, offset=msg_offset)
            # message_count on the conversation row is the authority: it is what
            # the writer stored, so it stays right even when this page is short.
            total = int((record or {}).get("message_count") or 0)
            related = {
                "messages": [_jsonify(m) for m in msgs],
                "message_total": total,
                "message_offset": msg_offset,
                "message_returned": len(msgs),
                "has_more": msg_offset + len(msgs) < total,
                "chunks": [_jsonify(c) for c in Q.conversations.chunks_from_conversation(conn, item_id)],
            }
        elif kind == "memory":
            record = Q.memory.get_chunk(conn, item_id)
            related = {}
        elif kind == "entity":
            record = Q.entities.get_entity(conn, item_id)
            related = {"relations": [_jsonify(r) for r in Q.entities.entity_relations(conn, item_id)]}
        elif kind == "project":
            record = Q.skills.get_project(conn, item_id)
            related = {}
        elif kind == "skill":
            record = Q.skills.get_skill(conn, item_id)
            related = {}
        else:
            record = Q.skills.get_prompt(conn, item_id)
            related = {}

    if record is None:
        raise HTTPException(status_code=404, detail=f"No {kind} with id {item_id}")
    return {"kind": kind, "record": _jsonify(record), "related": related}
