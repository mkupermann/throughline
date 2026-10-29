"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from ..settings import Settings


def get_settings(request: Request) -> Settings:
    """The Settings instance stashed on app state by the factory."""
    return request.app.state.settings
