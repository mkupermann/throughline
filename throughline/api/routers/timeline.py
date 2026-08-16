"""Timeline endpoints.

The Timeline is its own surface, not a view mode of search results — which is
what made it inherit pagination and show one page of a range.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from throughline.queries import timeline as T

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["timeline"])

MAX_DETAIL = 500


@router.get("/timeline")
def get_timeline(
    since: date | None = Query(None),
    until: date | None = Query(None),
    bucket: str | None = Query(None, pattern="^(day|week|month)$"),
    kind: list[str] = Query(default=[]),
    provider: list[str] = Query(default=[]),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    until = until or date.today()
    since = since or (until - timedelta(days=89))
    chosen = bucket or T.pick_bucket(since, until)
    with connection(settings) as conn:
        cells = T.aggregate(conn, since, until, chosen, kinds=kind, providers=provider)
    return {"since": since, "until": until, "bucket": chosen, "cells": cells}


@router.get("/timeline/day/{day}")
def get_timeline_day(
    day: date,
    kind: list[str] = Query(default=[]),
    provider: list[str] = Query(default=[]),
    limit: int = Query(100, ge=1, le=MAX_DETAIL),
    offset: int = Query(0, ge=0),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    with connection(settings) as conn:
        items = T.day_detail(conn, day, kinds=kind, providers=provider, limit=limit, offset=offset)
    return {"day": day, "items": items}
