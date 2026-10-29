"""The /console surface: read-only SQL."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from throughline.queries import console as C

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["console"])


class QueryRequest(BaseModel):
    sql: str = Field(..., max_length=100_000)
    timeout_ms: int = Field(default=C.DEFAULT_TIMEOUT_MS, ge=100, le=60_000)
    max_rows: int = Field(default=C.DEFAULT_MAX_ROWS, ge=1, le=10_000)


@router.post("/console/query")
def query(
    body: QueryRequest = Body(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Run a statement read-only.

    Always 200: a SQL error is a normal result of using a console, and is
    returned in the body so the editor can show it inline rather than as a
    failed request.
    """
    with connection(settings) as conn:
        result = C.run_query(conn, body.sql, timeout_ms=body.timeout_ms, max_rows=body.max_rows)
    return asdict(result)


@router.get("/console/schema")
def schema(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        data = C.schema(conn)
    return {**data, "snippets": C.SNIPPETS}
