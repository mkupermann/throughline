"""HTTP API for Throughline.

Reads exclusively through ``throughline.queries`` — the same layer the CLI
and the MCP server use — so a query is written and tuned once.

Import is lazy: pulling FastAPI and uvicorn into every ``throughline`` import
would slow the CLI and the MCP server down for no reason, so the app factory
is only loaded when something actually serves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .app import create_app
    from .settings import Settings

__all__ = ["create_app", "Settings", "serve"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "Settings":
        from .settings import Settings

        return Settings
    if name == "serve":
        from .server import serve

        return serve
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
