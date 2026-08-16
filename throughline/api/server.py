"""uvicorn entry point behind ``throughline serve``."""

from __future__ import annotations

import logging

from .settings import Settings, check_bind_allowed


def serve(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
    log_level: str = "info",
) -> int:
    """Run the API (and the built frontend) on one port. Returns an exit code."""
    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed — it is a core dependency.\n  pip install -e .",
        )
        return 1

    base = Settings.from_env()
    settings = Settings(
        host=host or base.host,
        port=port or base.port,
        web_dist=base.web_dist,
        pool_min=base.pool_min,
        pool_max=base.pool_max,
        redact=base.redact,
    )

    check_bind_allowed(settings.host)
    logging.basicConfig(level=log_level.upper())

    if settings.web_dist is None:
        print(
            "No built frontend found — serving the API only.\n  npm --prefix web install && npm --prefix web run build"
        )
    print(f"Throughline → http://{settings.host}:{settings.port}")

    if reload:
        # --reload needs an import string; the app then builds its own
        # Settings from the environment in the worker process.
        import os

        os.environ.setdefault("THROUGHLINE_HOST", settings.host)
        os.environ.setdefault("THROUGHLINE_PORT", str(settings.port))
        uvicorn.run(
            "throughline.api.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=True,
            log_level=log_level,
        )
        return 0

    from .app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=log_level,
    )
    return 0
