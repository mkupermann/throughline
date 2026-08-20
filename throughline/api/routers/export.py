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
    return {
        "root": str(root),
        "suggested": str(root / _SUGGESTED_FOLDER),
        "job": "export-markdown",
        "defaults": {
            "includeGenerated": False,
            "redact": False,
            "toolOutput": 0,
            "memory": True,
        },
    }


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
