"""FastAPI application factory.

Serves the JSON API under ``/api`` and, when a built frontend is present,
the SPA from the same process on the same port. One command, one port —
``throughline serve``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from throughline import __version__
from throughline.jobs.pm_watch import poll_all_running

from . import deps
from .deps import DatabaseUnavailable, close_pool, init_pool
from .routers import (
    ask,
    console,
    curate,
    export,
    find,
    operate,
    overview,
    pm,
    projects,
    providers,
    timeline,
)
from .settings import Settings

log = logging.getLogger("throughline.api")

#: Filenames served straight from web/dist root rather than /assets.
_ROOT_STATIC = ("favicon.ico", "favicon.svg", "robots.txt", "manifest.webmanifest")

#: How often the pm watch loop polls running tasks for log/status changes.
_PM_WATCH_INTERVAL_SECONDS = 10


async def _pm_watch_loop(settings: Settings) -> None:
    """Poll every running pm task forever, one tick per interval.

    A bad tick must never kill the loop — the next tick tries again, and
    `list_running_tasks` is a cheap query even right after a failure.
    """
    while True:
        try:
            with deps.connection(settings) as conn:
                poll_all_running(conn)
        except Exception:
            log.exception("pm watch loop tick failed")
        await asyncio.sleep(_PM_WATCH_INTERVAL_SECONDS)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_pool(settings)
        watch_task = asyncio.create_task(_pm_watch_loop(settings))
        app.state.pm_watch_task = watch_task
        try:
            yield
        finally:
            watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await watch_task
            close_pool()

    app = FastAPI(
        title="Throughline",
        version=__version__,
        summary="Persistent long-term memory across AI coding tools",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings

    @app.exception_handler(DatabaseUnavailable)
    async def _db_unavailable(request: Request, exc: DatabaseUnavailable):
        """A down database is a 503 with an explanation, never an opaque 500."""
        return JSONResponse(
            status_code=503,
            content={
                "error": "database_unavailable",
                "detail": str(exc),
                "hint": (
                    "Is PostgreSQL running? Check PGHOST/PGPORT/PGDATABASE, or run `docker compose up -d postgres`."
                ),
            },
        )

    app.include_router(overview.router, prefix="/api")
    app.include_router(find.router, prefix="/api")
    app.include_router(curate.router, prefix="/api")
    app.include_router(operate.router, prefix="/api")
    app.include_router(console.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(timeline.router, prefix="/api")
    app.include_router(ask.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    app.include_router(pm.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Readiness: only report healthy after PostgreSQL accepts a query."""
        with deps.connection(settings) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "ok", "version": __version__}

    _mount_frontend(app, settings.web_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path | None) -> None:
    """Serve the built SPA, if there is one.

    Absent during backend-only development: the API keeps working and only
    the non-/api routes 404, with a message pointing at the build command
    rather than a bare Not Found.
    """
    if dist is None or not dist.is_dir():

        @app.get("/{full_path:path}")
        def _no_frontend(full_path: str):
            return JSONResponse(
                status_code=404,
                content={
                    "error": "frontend_not_built",
                    "detail": "No built frontend found.",
                    "hint": "Run `npm --prefix web install && npm --prefix web run build`.",
                },
            )

        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = dist / "index.html"

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """SPA fallback: real files win, everything else gets index.html.

        Client-side routes like /find or /c/42 have no file behind them and
        must still return the app shell so a deep link or a refresh works.
        """
        if full_path in _ROOT_STATIC:
            candidate = dist / full_path
            if candidate.is_file():
                return FileResponse(candidate)
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return FileResponse(index)
