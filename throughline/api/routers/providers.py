"""Provider coverage — one endpoint, three consumers.

The provider bar, the Overview attention item and the Operate table all read
this. One source, so they cannot disagree about whether Hermes is imported.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from throughline.queries import providers as Q

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["providers"])


@router.get("/providers")
def list_providers(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        return {"providers": Q.coverage(conn)}
