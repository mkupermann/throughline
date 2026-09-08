"""PostgreSQL-backed queue. Submitted options contain no provider/CLI credentials."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import Json

from throughline.config import get_db_config
from throughline.queries._exec import one, rows

SUBMIT_LOCK = 139401
WORKER_LOCK = 139402
EXPORT_OPTIONS = frozenset(
    {
        "THROUGHLINE_EXPORT_OUT",
        "THROUGHLINE_EXPORT_PROJECT",
        "THROUGHLINE_EXPORT_SINCE",
        "THROUGHLINE_EXPORT_INCLUDE_GENERATED",
        "THROUGHLINE_EXPORT_REDACT",
        "THROUGHLINE_EXPORT_TOOL_OUTPUT",
        "THROUGHLINE_EXPORT_NO_MEMORY",
    }
)


@contextmanager
def database():
    from .deps import DatabaseUnavailable

    try:
        conn = psycopg2.connect(**{**get_db_config(), "connect_timeout": 3})
    except psycopg2.Error as exc:
        raise DatabaseUnavailable("Processing database unavailable.") from exc
    try:
        with conn:
            from .access import actor_context

            context = actor_context.get()
            if context:
                one(
                    conn,
                    "SELECT set_config('throughline.actor', %s, true), set_config('throughline.request_id', %s, true)",
                    context,
                )
            yield conn
    finally:
        conn.close()


def snapshot(row):
    now = time.time()
    started = (row["started_at"] or row["created_at"]).timestamp()
    finished = row["finished_at"].timestamp() if row["finished_at"] else None
    return {
        "id": row["id"],
        "name": row["name"],
        "running": row["state"] in {"queued", "running"},
        "state": row["state"],
        "returncode": row["returncode"],
        "started_at": started,
        "finished_at": finished,
        "duration_s": round((finished or now) - started, 1),
        "lines": row["lines"],
        "dropped_lines": row["dropped_lines"],
        "error": row["error"],
        "stages": row["stages"],
        "recoveries": row["recoveries"],
        "stop_requested": row["stop_requested"],
        "requested_by": row["requested_by"],
    }


class StoredJob:
    def __init__(self, row):
        self.id, self.name = row["id"], row["name"]

    def snapshot(self):
        with database() as conn:
            row = one(conn, "SELECT * FROM processing_runs WHERE id=%s", (self.id,))
        return snapshot(row)

    @property
    def running(self):
        return self.snapshot()["running"]


class DurableJobRunner:
    """The public job API remains compatible with the in-memory development runner."""

    def start(self, name, extra_env=None):
        from .access import actor_context
        from .jobs import JOBS, JobUnavailable, check_requirement

        if name not in JOBS:
            raise KeyError(name)
        options = extra_env or {}
        if options and (name != "export-markdown" or set(options) - EXPORT_OPTIONS):
            raise ValueError("Only registered export options can be persisted.")
        if any(not isinstance(value, str) or len(value) > 4096 for value in options.values()):
            raise ValueError("Invalid export options.")
        unmet = check_requirement(JOBS[name].requires)
        if unmet:
            raise JobUnavailable(unmet)
        actor, request_id = actor_context.get() or ("operator", None)
        with database() as conn:
            one(conn, "SELECT pg_advisory_xact_lock(%s)", (SUBMIT_LOCK,))
            if name != "process-all" and one(
                conn, "SELECT id FROM processing_runs WHERE name='process-all' AND state IN ('queued','running')"
            ):
                raise JobUnavailable("The complete pass is queued or running. Open its progress or stop it first.")
            existing = one(
                conn, "SELECT * FROM processing_runs WHERE name=%s AND state IN ('queued','running')", (name,)
            )
            if existing:
                job = StoredJob(existing)
            else:
                row = one(
                    conn,
                    """INSERT INTO processing_runs(id,name,options,requested_by,request_id)
                    VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                    (uuid.uuid4().hex, name, Json(options), actor, request_id),
                )
                job = StoredJob(row)
        self.kick()
        return job

    def kick(self):
        """An independently supervised worker can also run this module directly.

        The database lock, not the local process list, determines ownership.
        A failed worker launch leaves the request safely queued for the next tick.
        """
        with database() as conn:
            pending = one(conn, "SELECT id FROM processing_runs WHERE state IN ('queued','running') LIMIT 1")
            if not pending:
                return
            available = one(conn, "SELECT pg_try_advisory_lock(%s) AS free", (WORKER_LOCK,))["free"]
            if not available:
                return
        try:
            subprocess.Popen(
                [sys.executable, "-m", "throughline.jobs.processing_worker"],
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=None,
                start_new_session=True,
            )
        except OSError:
            with database() as conn:
                one(
                    conn,
                    "UPDATE processing_runs SET error='Worker launch failed; queued work will be retried. Inspect server logs.' WHERE state='queued'",
                )
            return

    def current(self, name):
        with database() as conn:
            row = one(conn, "SELECT * FROM processing_runs WHERE name=%s AND state IN ('queued','running')", (name,))
        return StoredJob(row) if row else None

    def get(self, job_id):
        with database() as conn:
            row = one(conn, "SELECT * FROM processing_runs WHERE id=%s", (job_id,))
        return StoredJob(row) if row else None

    def history(self):
        with database() as conn:
            records = rows(conn, "SELECT * FROM processing_runs ORDER BY created_at DESC,id DESC LIMIT 50")
        result = []
        for record in records:
            value = snapshot(record)
            value.pop("lines", None)
            result.append(value)
        return result

    def stop(self, job_id):
        with database() as conn:
            row = one(
                conn,
                """UPDATE processing_runs SET stop_requested=true,
                state=CASE WHEN state='queued' THEN 'stopped' ELSE state END,
                finished_at=CASE WHEN state='queued' THEN now() ELSE finished_at END,
                returncode=CASE WHEN state='queued' THEN -15 ELSE returncode END
                WHERE id=%s AND state IN ('queued','running') RETURNING id""",
                (job_id,),
            )
        return bool(row)

    def stream(self, job, poll=0.5):
        from .jobs import _sse

        sent = 0
        while True:
            snap = job.snapshot()
            for line in snap["lines"][max(0, sent - snap["dropped_lines"]) :]:
                yield _sse("line", line)
            sent = snap["dropped_lines"] + len(snap["lines"])
            if not snap["running"]:
                yield _sse("done", f"exit={snap['returncode']} duration={snap['duration_s']}s")
                return
            yield ": keepalive\n\n"
            time.sleep(poll)
