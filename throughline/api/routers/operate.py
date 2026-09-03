"""The /operate surface: pipeline state and the jobs that change it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from throughline import embedding
from throughline import queries as Q
from throughline.config import get_db_config
from throughline.queries import providers as provider_queries
from throughline.scheduler import status as scheduler_status
from throughline.status import collect_status

from ..deps import connection
from ..jobs import JOBS, JobUnavailable, check_requirement, runner
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["operate"])

#: Jobs this page must not offer as a bare Run button, because they need
#: input the button cannot supply. The Markdown export needs a destination;
#: it has its own panel and its own endpoint.
HIDDEN_JOBS = frozenset({"export-markdown"})


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def generation_panel() -> dict[str, Any]:
    """Which model generates, and whether it runs here.

    The page already showed the embedding backend. It said nothing about the
    one that extracts memory, writes titles and answers questions — the model
    whose choice decides whether transcripts leave the machine. That was
    visible only from `throughline doctor`, which is the wrong place for a
    fact the operating surface is otherwise built to show.
    """
    from throughline import llm

    info = llm.backend_info()
    return {
        "available": info.available,
        "backend": info.backend,
        "model": info.model,
        "local": info.local,
        "detail": info.detail,
    }


def _matches_job(name: str, stage: str) -> bool:
    if stage == "ingest":
        return name == "ingest" or name.startswith("ingest_")
    return name == stage


def _last_success(history: list[dict[str, Any]], matches: Callable[[str], bool]) -> str | None:
    successful = [run for run in history if matches(str(run.get("name") or "")) and run.get("returncode") == 0]
    if not successful:
        return None
    run = max(successful, key=_history_sort_key)
    finished = run.get("finished_at")
    if isinstance(finished, (int, float)):
        return datetime.fromtimestamp(finished, timezone.utc).isoformat()
    return _iso(finished) if finished else None


def _latest_failure(history: list[dict[str, Any]], matches: Callable[[str], bool]) -> dict[str, Any] | None:
    """Return the latest failed run, but never an older superseded failure."""
    completed = [run for run in history if matches(str(run.get("name") or "")) and not run.get("running")]
    if not completed:
        return None
    latest = max(completed, key=_history_sort_key)
    return None if latest.get("returncode") == 0 else latest


def _event_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not parsed.tzinfo:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def _history_sort_key(run: dict[str, Any]) -> float:
    timestamp = _event_timestamp(run.get("finished_at") or run.get("started_at"))
    return timestamp if timestamp is not None else float("-inf")


def _failure_reason(run: dict[str, Any]) -> str:
    if run.get("error"):
        return str(run["error"])
    code = run.get("returncode")
    return f"Last run exited with code {code}." if code is not None else "The last run did not finish."


def _runtime_stage(
    *,
    key: str,
    label: str,
    job_name: str,
    action_label: str,
    due: bool,
    due_detail: str,
    healthy_detail: str,
    jobs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    persisted_success: Any = None,
) -> dict[str, Any]:
    def matches(name: str) -> bool:
        return _matches_job(name, job_name)

    related_jobs = [job for job in jobs if matches(str(job.get("name") or ""))]
    running = next((job for job in related_jobs if job.get("running")), None)
    canonical = next((job for job in related_jobs if job.get("name") == job_name), None)
    unavailable = canonical.get("unavailable") if canonical else None
    failure = _latest_failure(history, matches)
    history_success = _last_success(history, matches)
    persisted_iso = _iso(persisted_success) if persisted_success else None
    history_success_at = _event_timestamp(history_success)
    persisted_success_at = _event_timestamp(persisted_success)
    last_success = (
        history_success
        if history_success and (persisted_success_at is None or (history_success_at or 0) > persisted_success_at)
        else persisted_iso
    )
    if failure and persisted_success_at is not None:
        failure_at = _history_sort_key(failure)
        if failure_at <= persisted_success_at:
            failure = None

    if running:
        state = "running"
        detail = f"{label} is running now."
        blocked_reason = None
    elif unavailable:
        state = "blocked"
        detail = f"{label} cannot run in the current environment."
        blocked_reason = str(unavailable)
    elif failure:
        state = "failed"
        detail = f"The last {label.lower()} run failed."
        blocked_reason = _failure_reason(failure)
    else:
        state = "due" if due else "healthy"
        detail = due_detail if due else healthy_detail
        blocked_reason = None

    return {
        "key": key,
        "label": label,
        "state": state,
        "detail": detail,
        "last_success": last_success,
        "blocked_reason": blocked_reason,
        "job_name": str(running.get("name")) if running else job_name,
        "job_id": running.get("job_id") if running else None,
        "action_label": (f"Retry {label.lower()}" if state == "failed" else action_label),
        "action_href": None,
    }


def derive_pipeline_stages(
    *,
    provider_coverage: list[dict[str, Any]],
    pending: dict[str, int],
    embedding_coverage: dict[str, int],
    vector_ok: bool,
    jobs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    snapshot: dict[str, Any],
    last_ingestion_at: Any,
    last_embedding_at: Any,
    audit_findings_available: bool,
    drift_findings: int,
) -> list[dict[str, Any]]:
    """Derive the five recoverable knowledge stages from existing state."""
    real_providers = [p for p in provider_coverage if p.get("name") != "(unattributed)"]
    scan_errors = [p for p in real_providers if p.get("status") == "unknown"]
    files = sum(int(p.get("on_disk") or 0) for p in real_providers)
    tools = sum(1 for p in real_providers if int(p.get("on_disk") or 0) > 0)
    if scan_errors:
        labels = ", ".join(str(p.get("label") or p.get("name")) for p in scan_errors)
        discover = {
            "key": "discover",
            "label": "Discover sources",
            "state": "failed",
            "detail": "One or more AI history locations could not be inspected.",
            "last_success": None,
            "blocked_reason": f"Could not inspect: {labels}.",
            "job_name": "doctor",
            "job_id": None,
            "action_label": "Run diagnostics",
            "action_href": None,
        }
    else:
        discover = {
            "key": "discover",
            "label": "Discover sources",
            "state": "healthy",
            "detail": (
                f"Found {files} session file{'s' if files != 1 else ''} across "
                f"{tools} tool{'s' if tools != 1 else ''}."
                if files
                else "No supported AI session files were found on this machine."
            ),
            "last_success": snapshot.get("captured_at"),
            "blocked_reason": None,
            "job_name": None,
            "job_id": None,
            "action_label": "View sources",
            "action_href": "#provider-coverage",
        }

    waiting_ingest = sum(int(p.get("pending") or 0) for p in real_providers)
    ingest = _runtime_stage(
        key="ingest",
        label="Ingest sessions",
        job_name="ingest",
        action_label="Ingest sessions",
        due=waiting_ingest > 0,
        due_detail=f"{waiting_ingest} session file{'s are' if waiting_ingest != 1 else ' is'} waiting to import.",
        healthy_detail="All discovered session files are imported.",
        jobs=jobs,
        history=history,
        persisted_success=last_ingestion_at,
    )

    waiting_extract = int(pending.get("extraction") or 0)
    extract = _runtime_stage(
        key="extract",
        label="Extract knowledge",
        job_name="extract",
        action_label="Extract knowledge",
        due=waiting_extract > 0,
        due_detail=f"{waiting_extract} conversation{'s are' if waiting_extract != 1 else ' is'} waiting for extraction.",
        healthy_detail="Every eligible conversation has extracted knowledge.",
        jobs=jobs,
        history=history,
        persisted_success=snapshot.get("last_extraction_at"),
    )

    total = int(embedding_coverage.get("total") or 0)
    embedded = int(embedding_coverage.get("embedded") or 0)
    missing_embeddings = max(0, total - embedded)
    embed = _runtime_stage(
        key="embed",
        label="Create embeddings",
        job_name="embed",
        action_label="Create embeddings",
        due=missing_embeddings > 0,
        due_detail=f"{missing_embeddings} active chunk{'s need' if missing_embeddings != 1 else ' needs'} an embedding.",
        healthy_detail="Every active memory chunk is available to semantic search.",
        jobs=jobs,
        history=history,
        persisted_success=last_embedding_at,
    )
    if not vector_ok and embed["state"] != "running":
        embed["state"] = "blocked"
        embed["detail"] = "Semantic indexing cannot use the current database extension."
        embed["blocked_reason"] = "pgvector is unavailable."

    sampled = int(snapshot.get("last_audit_sampled") or 0)
    drifted = int(snapshot.get("last_audit_drifted") or 0)
    last_audit = snapshot.get("last_audit_at")
    legacy_findings = bool(last_audit and drifted and not audit_findings_available)
    review_due = not last_audit or sampled == 0 or legacy_findings or drift_findings > 0
    review = _runtime_stage(
        key="review",
        label="Review quality",
        job_name="audit-extraction",
        action_label="Run drift audit",
        due=review_due,
        due_detail=(
            f"{drift_findings} drift finding{'s need' if drift_findings != 1 else ' needs'} attention."
            if drift_findings
            else (
                "The last audit predates reviewable findings. Run a current audit."
                if legacy_findings
                else (
                    "No eligible memory was checked in the last audit."
                    if last_audit
                    else "No memory drift audit has run yet."
                )
            )
        ),
        healthy_detail=(
            "All findings from the last audit have been resolved."
            if drifted and audit_findings_available
            else f"The last audit checked {sampled} chunks and found no drift."
        ),
        jobs=jobs,
        history=history,
        persisted_success=last_audit,
    )
    if drift_findings > 0 and review["state"] == "due":
        review["action_label"] = "Review findings"
        review["action_href"] = "/curate?queue=drift"

    return [discover, ingest, extract, embed, review]


@router.get("/operate/status")
def status(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    backend = embedding.backend_info()
    active_model = backend.model if backend.available else None
    active_column = backend.column if backend.available else None
    with connection(settings) as conn:
        counts = Q.health.pipeline_counts(conn)
        vector_ok = Q.health.vector_extension_ok(conn)
        coverage = (
            Q.health.embedding_coverage(conn, active_model, active_column) if vector_ok else {"total": 0, "embedded": 0}
        )
        by_model = Q.health.embeddings_by_model(conn) if vector_ok else []
        recent = Q.health.recent_ingestion(conn, limit=20)
        snapshot = collect_status(conn=conn)
        pending = Q.health.pending_extraction(conn, min_messages=5)
        titles = Q.health.missing_titles(conn, min_messages=2)
        provider_coverage = provider_queries.coverage(conn)
        last_ingestion = Q.health.last_ingestion_at(conn)
        last_embedding = Q.health.last_embedding_at(conn, active_model, active_column) if vector_ok else None
        latest_audit = Q.curate.latest_audit(conn)
        drift_findings = Q.curate.drift_count(conn, latest_audit)

    requirement_status: dict[str | None, str | None] = {None: None}
    job_summaries = []
    for spec in JOBS.values():
        if spec.name in HIDDEN_JOBS:
            continue
        if spec.requires not in requirement_status:
            requirement_status[spec.requires] = check_requirement(spec.requires)
        current = runner.current(spec.name)
        job_summaries.append(
            {
                "name": spec.name,
                "title": spec.title,
                "description": spec.description,
                "danger": spec.danger,
                "running": bool(current),
                "job_id": current.id if current else None,
                "unavailable": requirement_status[spec.requires],
            }
        )
    history = runner.history()
    pending_state = {"extraction": pending, "titles": titles}
    embedding_state = {
        "total": int(coverage.get("total") or 0),
        "embedded": int(coverage.get("embedded") or 0),
    }
    pipeline = derive_pipeline_stages(
        provider_coverage=provider_coverage,
        pending=pending_state,
        embedding_coverage=embedding_state,
        vector_ok=vector_ok,
        jobs=job_summaries,
        history=history,
        snapshot=snapshot,
        last_ingestion_at=last_ingestion,
        last_embedding_at=last_embedding,
        audit_findings_available=bool(latest_audit and latest_audit.get("findings_available")),
        drift_findings=drift_findings,
    )

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
            "coverage": embedding_state,
            "by_model": [{k: _iso(v) for k, v in r.items()} for r in by_model],
        },
        "generation": generation_panel(),
        "pending": pending_state,
        "ingestion": [{k: _iso(v) for k, v in r.items()} for r in recent],
        "providers": [{k: _iso(v) for k, v in row.items()} for row in provider_coverage],
        "pipeline": pipeline,
        "jobs": job_summaries,
        "history": history,
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
