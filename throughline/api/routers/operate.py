"""The /operate surface: pipeline state and the jobs that change it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from throughline import embedding
from throughline import queries as Q
from throughline.config import get_db_config
from throughline.scheduler import status as scheduler_status
from throughline.status import collect_status

from ..deps import connection
from ..jobs import JOBS, JobUnavailable, check_requirement, runner
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["operate"])


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/operate/status")
def status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        counts = Q.health.pipeline_counts(conn)
        vector_ok = Q.health.vector_extension_ok(conn)
        coverage = Q.health.embedding_coverage(conn) if vector_ok else {"total": 0, "embedded": 0}
        by_model = Q.health.embeddings_by_model(conn) if vector_ok else []
        recent = Q.health.recent_ingestion(conn, limit=20)
        snapshot = collect_status(conn=conn)
        pending = Q.health.pending_extraction(conn, min_messages=5)
        titles = Q.health.missing_titles(conn, min_messages=2)

    backend = embedding.backend_info()

    return {
        "counts": {k: int(v or 0) for k, v in (counts or {}).items()},
        # collect_status reports on the *contents* of the database, not on how
        # it was reached — the connection details come from the same config
        # helper the pool uses, so this panel cannot drift from reality.
        "database": {
            **{k: v for k, v in get_db_config().items() if k != "password"},
            "reachable": snapshot.get("db_reachable", False),
            "schema_version": snapshot.get("schema_version"),
            "tables": snapshot.get("table_row_counts", {}),
        },
        "extensions": {
            "pgvector_usable": vector_ok,
            # The catalogue can list pgvector while its library is missing —
            # exactly what a Homebrew major-version bump does. Say which.
            "note": (
                None
                if vector_ok
                else (
                    "pgvector is registered but its shared library cannot be loaded. "
                    "Every query touching a vector column fails. Reinstall pgvector "
                    "for this PostgreSQL major version."
                )
            ),
        },
        "embedding": {
            "backend": backend.label,
            "available": backend.available,
            "reason": backend.reason or None,
            "coverage": {
                "total": int(coverage.get("total") or 0),
                "embedded": int(coverage.get("embedded") or 0),
            },
            "by_model": [{k: _iso(v) for k, v in r.items()} for r in by_model],
        },
        "pending": {"extraction": pending, "titles": titles},
        "ingestion": [{k: _iso(v) for k, v in r.items()} for r in recent],
        "jobs": [
            {
                "name": spec.name,
                "title": spec.title,
                "description": spec.description,
                "danger": spec.danger,
                "running": bool(runner.current(spec.name)),
                "job_id": (runner.current(spec.name).id if runner.current(spec.name) else None),
                # Say up front when a job cannot run here, instead of letting
                # the user click Run and read the failure afterwards.
                "unavailable": check_requirement(spec.requires),
            }
            for spec in JOBS.values()
        ],
        "history": runner.history(),
        # The external launchd scheduler skill — separate from the pipeline
        # jobs above, and read-only here.
        "scheduler": scheduler_status(),
    }


@router.post("/operate/run/{name}")
def run(name: str) -> dict[str, Any]:
    """Start a job, or return the one already running under that name."""
    try:
        job = runner.start(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job {name!r}") from exc
    except JobUnavailable as exc:
        # 409: the request is well-formed, the environment cannot serve it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    snap = job.snapshot()
    return {"job_id": job.id, "name": job.name, "running": snap["running"]}


@router.post("/operate/stop/{job_id}")
def stop(job_id: str) -> dict[str, Any]:
    if not runner.stop(job_id):
        raise HTTPException(status_code=404, detail="No running job with that id")
    return {"stopped": job_id}


@router.get("/operate/job/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    j = runner.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return j.snapshot()


@router.get("/operate/job/{job_id}/stream")
def stream(job_id: str) -> StreamingResponse:
    """Live job output as server-sent events.

    A watcher that connects late still sees the whole run: the stream replays
    the retained buffer before going live.
    """
    j = runner.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return StreamingResponse(
        runner.stream(j),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
