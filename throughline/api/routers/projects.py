"""Projects, and the sessions inside them.

Three levels — project, session, message — because the flat alternative does
not survive the data: one project holds 7,284 messages and a single session
holds 5,560. A project view lists its sessions; opening a session lists its
messages, which is already paginated.

Everything here shows what a person did. Machine-generated conversations are
excluded by default and counted separately, so the omission is visible rather
than silent: on the corpus this was built against, 3,017 of 3,606 stored
conversations were the tool's own `claude -p` calls.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from throughline.queries import projects as Q

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["projects"])

#: Sessions per page. Large enough that most projects fit in one request —
#: the busiest on this corpus has 36 in a week — small enough that a project
#: with years of history does not arrive in one payload.
PAGE = 50


@router.get("/projects/recent")
def recent(
    days: int = Query(7, ge=1, le=365),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Projects touched in the last *days*, busiest first."""
    with connection(settings) as conn:
        return {"days": days, "projects": Q.recent(conn, days=days)}


@router.get("/projects/{name:path}/sessions")
def sessions(
    name: str,
    order: Literal["newest", "oldest"] = "newest",
    q: str | None = Query(None, max_length=200, description="Search within this project."),
    limit: int = Query(PAGE, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_generated: bool = Query(
        False,
        description="Also list sessions a script produced. Off by default; the user decides, the interface does not.",
    ),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """One page of sessions, ordered and optionally filtered by a search term.

    `{name:path}` because a project name is the last segment of a working
    directory and can contain characters — spaces, dots — that a stricter
    converter would reject. "The FireScore Website" is a real project on this
    corpus.
    """
    term = (q or "").strip() or None
    with connection(settings) as conn:
        rows = Q.sessions(
            conn,
            name,
            order=order,
            q=term,
            limit=limit,
            offset=offset,
            include_generated=include_generated,
        )
        total = Q.session_count(conn, name, q=term, include_generated=include_generated)
        return {
            "project": name,
            "order": order,
            "q": term,
            "include_generated": include_generated,
            "sessions": rows,
            "total": total,
            "offset": offset,
            "has_more": offset + len(rows) < total,
            # Stated rather than silently dropped: a project whose list shows
            # 28 sessions out of 660 stored rows needs to say where the rest
            # went, or the interface is lying about how much is kept.
            "hidden_generated": Q.hidden_count(conn, name),
        }
