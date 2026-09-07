"""Project story endpoints; writes are explicit user-authored checkpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from throughline.queries import story as Q

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(prefix="/story", tags=["project story"])


class Checkpoint(BaseModel):
    path: str | None = Field(None, max_length=4096)
    kind: Literal["goal", "status", "blocker", "next"]
    content: str = Field(min_length=1, max_length=4000)
    conversation_id: int = Field(gt=0)
    message_id: int | None = Field(None, gt=0)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("A checkpoint cannot be blank.")
        return value


@router.get("/{project:path}/history")
def history(
    project: str,
    path: str | None = None,
    generated: bool = False,
    provider: list[str] = Query(default=[]),
    q: str = Query("", max_length=200),
    order: Literal["oldest", "newest"] = "newest",
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    settings: Settings = Depends(get_settings),
):
    with connection(settings) as conn:
        return Q.history(
            conn,
            project,
            path=path,
            generated=generated,
            q=q.strip(),
            order=order,
            offset=offset,
            limit=limit,
            providers=provider,
        )


@router.get("/{project:path}/session/{conversation_id}")
def session(
    project: str,
    conversation_id: int,
    path: str | None = None,
    generated: bool = False,
    provider: list[str] = Query(default=[]),
    q: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    settings: Settings = Depends(get_settings),
):
    with connection(settings) as conn:
        result = Q.session_detail(
            conn,
            project,
            conversation_id,
            path=path,
            generated=generated,
            q=q.strip(),
            offset=offset,
            limit=limit,
            providers=provider,
        )
    if result is None:
        raise HTTPException(404, "Session does not belong to this project scope.")
    return result


@router.post("/{project:path}/checkpoints", status_code=201)
def checkpoint(project: str, body: Checkpoint, settings: Settings = Depends(get_settings)):
    with connection(settings) as conn:
        result = Q.checkpoint(conn, project, body)
    if result is None:
        raise HTTPException(422, "Choose an existing source in this project scope.")
    return result


@router.get("/{project:path}/session/{conversation_id}/artifact/{message_id}/{index}")
def artifact(
    project: str,
    conversation_id: int,
    message_id: int,
    index: int,
    path: str | None = None,
    generated: bool = False,
    provider: list[str] = Query(default=[]),
    settings: Settings = Depends(get_settings),
):
    """Download only an explicit source reference inside this conversation's folder."""
    from pathlib import Path

    from fastapi.responses import FileResponse

    from throughline.queries._exec import one
    from throughline.queries.presentation import artifacts

    predicate, params = Q.scope(project, path, generated, provider)
    params["id"] = conversation_id
    with connection(settings) as conn:
        session = one(conn, f"SELECT c.project_path FROM conversations c WHERE c.id=%(id)s AND {predicate}", params)
        if not session:
            raise HTTPException(404, "Conversation is outside this project scope.")
        refs = artifacts(conn, conversation_id, session["project_path"])
    if index < 0 or index >= len(refs):
        raise HTTPException(404, "File reference not found.")
    ref = refs[index]
    if ref["message_id"] != message_id or ref["availability"] != "available":
        raise HTTPException(404, "Referenced file is not accessible on this server.")
    root = Path(session["project_path"]).resolve()
    target = (root / ref["path"]).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "Referenced file is not accessible on this server.")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff"},
    )
