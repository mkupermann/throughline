"""Answer a question from the stored history, with citations.

Thin wrapper over `throughline.ask` — the retrieval, prompt and citation rules
live there so the CLI and this endpoint cannot drift into answering the same
question two different ways.

POST rather than GET: a question is prose, often long, and frequently contains
characters that make a URL unreadable. It also keeps questions out of access
logs and browser history, which matters for a tool whose whole subject is the
user's private working history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from throughline import ask as _ask

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    #: Bounded rather than free: the prompt has to fit a small model's context,
    #: and a caller asking for 500 records would silently get a truncated
    #: answer built from an arbitrary subset.
    top_k: int = Field(default=_ask.DEFAULT_TOP_K, ge=1, le=48)
    project: str | None = None
    #: Empty means "whatever throughline.llm picks" — naming a default here
    #: would pin the API to one vendor's model names and undo the point of a
    #: swappable backend.
    model: str = Field(default="", max_length=64)


@router.post("/ask")
def ask(body: AskRequest, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="A question is required.")

    with connection(settings) as conn:
        result = _ask.answer(
            conn,
            question,
            top_k=body.top_k,
            project=body.project,
            model=body.model or None,
        )
    # A degraded result is a 200 with `degraded` set, not an error: retrieval
    # may have succeeded while the model was unreachable, and those sources are
    # exactly what the reader would have looked up by hand. Returning 503 would
    # throw away the useful half.
    return result.to_dict()
