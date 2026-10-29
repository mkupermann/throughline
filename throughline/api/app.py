"""FastAPI application factory.

Serves the JSON API under ``/api`` and, when a built frontend is present,
the SPA from the same process on the same port. One command, one port —
``throughline serve``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from throughline import __version__

from . import deps
from .deps import DatabaseUnavailable, close_pool, init_pool
from .routers import (
    access,
    ai_settings,
    ask,
    assignments,
    console,
    curate,
    export,
    find,
    operate,
    overview,
    projects,
    providers,
    story,
    timeline,
)
from .settings import Settings

log = logging.getLogger("throughline.api")

#: Filenames served straight from web/dist root rather than /assets.
_ROOT_STATIC = ("favicon.ico", "favicon.svg", "robots.txt", "manifest.webmanifest")

#: How often the pm watch loop polls running tasks for log/status changes.
_PM_WATCH_INTERVAL_SECONDS = 10


def _pm_watch_tick(settings: Settings) -> None:
    """One synchronous poll of every running pm task: pooled psycopg2 calls
    and file reads, none of it awaitable. Run only via `asyncio.to_thread`
    (see `_pm_watch_loop`) — called directly on the event loop it would
    block every other request for the tick's duration."""
    # Imported here so a build with PM fenced off never loads pm_watch at all.
    from throughline.jobs.pm_watch import poll_all_running

    with deps.connection(settings) as conn:
        poll_all_running(conn)


async def _pm_watch_loop(settings: Settings) -> None:
    """Poll every running pm task forever, one tick per interval.

    A bad tick must never kill the loop — the next tick tries again, and
    `list_running_tasks` is a cheap query even right after a failure. The
    tick itself runs in a worker thread (`asyncio.to_thread`) so its
    synchronous DB + file I/O never stalls request handling on the event
    loop; `CancelledError` from either await (the thread or the sleep) is
    left to propagate so shutdown can cancel the loop promptly.
    """
    while True:
        try:
            await asyncio.to_thread(_pm_watch_tick, settings)
        except Exception:
            log.exception("pm watch loop tick failed")
        await asyncio.sleep(_PM_WATCH_INTERVAL_SECONDS)


async def _processing_loop():
    from .jobs import runner

    while True:
        try:
            if hasattr(runner, "kick"):
                await asyncio.to_thread(runner.kick)
        except Exception:
            log.exception("processing queue tick failed; persisted work will be retried")
        await asyncio.sleep(5)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_pool(settings)
        # PM is opt-in (THROUGHLINE_ENABLE_PM). When it is off there is no
        # watch loop and no pm_watch_task — the knowledge pipeline must not
        # depend on either existing. Processing is unconditional.
        watch_task: asyncio.Task[None] | None = None
        if settings.enable_pm:
            watch_task = asyncio.create_task(_pm_watch_loop(settings))
            app.state.pm_watch_task = watch_task
        processing_task = asyncio.create_task(_processing_loop())
        try:
            yield
        finally:
            processing_task.cancel()
            with suppress(asyncio.CancelledError):
                await processing_task
            if watch_task is not None:
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
    from .access import AccessMiddleware

    app.add_middleware(AccessMiddleware)
    app.include_router(access.router, prefix="/api")

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError):
        # Never echo a supplied password, API key or transcript in validation errors.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]} for error in exc.errors()
                ]
            },
        )

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

    app.include_router(assignments.router, prefix="/api")
    app.include_router(ai_settings.router, prefix="/api")
    app.include_router(overview.router, prefix="/api")
    app.include_router(find.router, prefix="/api")
    app.include_router(curate.router, prefix="/api")
    app.include_router(operate.router, prefix="/api")
    app.include_router(console.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(timeline.router, prefix="/api")
    app.include_router(ask.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(story.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    if settings.enable_pm:
        # Imported here so `import throughline.api.app` stays free of the PM
        # domain when the surface is fenced off.
        from .routers import pm as pm_router

        app.include_router(pm_router.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Readiness: only report healthy after PostgreSQL accepts a query."""
        with deps.connection(settings) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "ok", "version": __version__}

    _mount_frontend(app, settings.web_dist, settings)
    return app


def _inject_meta(html: str, settings: Settings | None) -> str:
    """Stamp runtime feature flags into the SPA shell.

    The built index.html is static, so the only way for a single committed
    bundle to know whether PM is on is a value the server writes at serve
    time. The frontend reads `window.__THROUGHLINE_META__` synchronously on
    boot — no fetch, no flash of a nav item the API will 404.
    """
    meta = {"features": {"pm": bool(settings.enable_pm) if settings else False}}
    payload = json.dumps(meta, separators=(",", ":"))
    script = f"<script>window.__THROUGHLINE_META__={payload};</script>"
    marker = "</head>"
    if marker in html:
        return html.replace(marker, f"{script}{marker}", 1)
    return f"{script}{html}"


def _mount_frontend(app: FastAPI, dist: Path | None, settings: Settings | None = None) -> None:
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
    if not index.is_file():
        # Partial/failed build: do not crash create_app — the API (and
        # /api/health) must stay up even when the SPA is missing.

        @app.get("/{full_path:path}")
        def _no_index(full_path: str):
            return JSONResponse(
                status_code=404,
                content={
                    "error": "frontend_not_built",
                    "detail": "index.html is missing from the built frontend.",
                    "hint": "Run `npm --prefix web install && npm --prefix web run build`.",
                },
            )

        return

    # Read once: the file is immutable for the life of the process, and every
    # deep-link refresh would otherwise re-read and re-inject.
    index_html = _inject_meta(index.read_text(encoding="utf-8"), settings)

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
        return HTMLResponse(index_html)
