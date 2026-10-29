"""The /export surface: write the corpus out as a Markdown vault.

Every other endpoint reads. This one writes files to a location the caller
names, which on an API with no authentication is a different kind of thing
entirely — so the destination is validated against a boundary
(``THROUGHLINE_EXPORT_ROOT``, the user's home by default) before anything
runs, and it travels to the job as an environment variable rather than as a
command-line argument. The job registry's guarantee is that no request body
becomes argv; this endpoint does not spend it.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from throughline.jobs import export_markdown as em

from ..jobs import JobUnavailable, runner

router = APIRouter(tags=["export"])

#: Where the suggested destination points when the caller has no preference.
_SUGGESTED_FOLDER = "Throughline-Export"


class ExportRequest(BaseModel):
    out: str = Field(description="Absolute destination directory, inside the export root.")
    project: str | None = Field(default=None, description="Export a single project instead of all.")
    since: str | None = Field(default=None, description="Only sessions started on or after YYYY-MM-DD.")
    includeGenerated: bool = Field(default=False, description="Include the tool's own model calls.")
    redact: bool = Field(default=False, description="Run every exported text through the PII pass.")
    toolOutput: int = Field(default=0, ge=0, le=20_000, description="Characters of tool output to keep.")
    memory: bool = Field(default=True, description="Write the per-project Memory.md file.")


@router.get("/export/markdown")
def options() -> dict[str, Any]:
    """What the UI needs to offer the export without guessing."""
    root = em.export_root()
    # In a container the destination is a container path, and the panel said
    # nothing about where that is on the machine the person is using. On
    # Windows they type C:\Users\… , are refused, and read it as the export
    # being broken — while a successful export lands somewhere they cannot
    # find. Compose passes the host side of the mount so the UI can say both.
    host_path = os.environ.get("THROUGHLINE_EXPORT_HOST_PATH", "").strip() or str(root)
    return {
        "root": str(root),
        "hostPath": host_path,
        "suggested": str(root / _SUGGESTED_FOLDER),
        "job": "export-markdown",
        "defaults": {
            "includeGenerated": False,
            "redact": False,
            "toolOutput": 0,
            "memory": True,
        },
    }


@router.get("/export/browse")
def browse(path: str | None = None) -> dict[str, Any]:
    """List the subdirectories of *path* (or the export root), for an in-app
    folder browser.

    Not a native OS dialog: the API may be running inside a container with
    no display at all (Docker Compose's `web` service, for one), where
    nothing server-side can ever open the host's real file picker — that is
    a hard OS boundary, not something this endpoint can paper over. Listing
    the confined root itself needs no display, though, and behaves
    identically in a container or a native install.
    """
    try:
        return em.list_directory(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/export/markdown")
def start(request: ExportRequest) -> dict[str, Any]:
    try:
        destination = em.resolve_destination(request.out)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    env = {em.DEST_ENV: str(destination)}
    if request.project:
        env["THROUGHLINE_EXPORT_PROJECT"] = request.project
    if request.since:
        env["THROUGHLINE_EXPORT_SINCE"] = request.since
    if request.includeGenerated:
        env["THROUGHLINE_EXPORT_INCLUDE_GENERATED"] = "1"
    if request.redact:
        env["THROUGHLINE_EXPORT_REDACT"] = "1"
    if request.toolOutput:
        env["THROUGHLINE_EXPORT_TOOL_OUTPUT"] = str(request.toolOutput)
    if not request.memory:
        env["THROUGHLINE_EXPORT_NO_MEMORY"] = "1"

    try:
        job = runner.start("export-markdown", extra_env=env)
    except JobUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:  # pragma: no cover - the registry entry is fixed
        raise HTTPException(status_code=500, detail="export-markdown job is not registered") from exc

    return {"out": str(destination), "job": {"id": job.id, "name": job.name, "running": job.running}}
