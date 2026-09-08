"""Single-owner queue worker. Finished full-pass steps survive process/container restarts."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

import psutil
from psycopg2.extras import Json

from throughline.api.durable_jobs import WORKER_LOCK, database
from throughline.queries._exec import one

RECOVERY_GRACE_SECONDS = 10


def claim(conn):
    record = one(
        conn,
        "SELECT * FROM processing_runs WHERE state IN ('queued','running') ORDER BY created_at,id LIMIT 1 FOR UPDATE",
    )
    if not record:
        return None
    if record["state"] == "running" and record["heartbeat_at"]:
        # The old guard has time to kill its command tree before a replacement starts.
        age = time.time() - record["heartbeat_at"].timestamp()
        if age < RECOVERY_GRACE_SECONDS:
            conn.commit()
            time.sleep(min(1, RECOVERY_GRACE_SECONDS - age))
            return {}
    if record["stop_requested"]:
        one(
            conn,
            "UPDATE processing_runs SET state='stopped',finished_at=now(),returncode=-15 WHERE id=%s",
            (record["id"],),
        )
        conn.commit()
        return {}
    recovered = record["state"] == "running"
    if recovered and record["recoveries"] >= 3:
        one(
            conn,
            "UPDATE processing_runs SET state='failed',finished_at=now(),returncode=1,error='Processing was interrupted repeatedly. Check the worker and database before starting another pass.' WHERE id=%s",
            (record["id"],),
        )
        conn.commit()
        return {}
    record = one(
        conn,
        """UPDATE processing_runs SET state='running',started_at=COALESCE(started_at,now()),
        heartbeat_at=now(),error=NULL,recoveries=recoveries+%s WHERE id=%s RETURNING *""",
        (int(recovered), record["id"]),
    )
    conn.commit()
    return record


def record_output(conn, job_id, lines):
    record = one(conn, "SELECT lines,stages,dropped_lines FROM processing_runs WHERE id=%s FOR UPDATE", (job_id,))
    combined = record["lines"] + [line[:8192] for line in lines]
    removed = max(0, len(combined) - 500)
    record = one(
        conn,
        """UPDATE processing_runs SET lines=%s,dropped_lines=dropped_lines+%s,
        heartbeat_at=now() WHERE id=%s RETURNING stop_requested""",
        (Json(combined[-500:]), removed, job_id),
    )
    conn.commit()
    return record["stop_requested"]


def execute(conn, record):
    from throughline.api.jobs import JOBS

    name, job_id = record["name"], record["id"]
    if name not in JOBS:
        one(
            conn,
            "UPDATE processing_runs SET state='failed',finished_at=now(),error='Job is not registered' WHERE id=%s",
            (job_id,),
        )
        conn.commit()
        return
    completed = [key for key, value in record["stages"].items() if value.get("state") == "finished"]
    env = {
        **os.environ,
        **record["options"],
        "PYTHONUNBUFFERED": "1",
        "THROUGHLINE_RESUME_STAGES": json.dumps(completed),
        "THROUGHLINE_PROCESSING_RUN_ID": job_id,
    }
    actor = record["requested_by"]
    request_id = record["request_id"] or job_id
    if re.fullmatch(r"operator|user:[0-9]+", actor) and re.fullmatch(r"[a-f0-9]{32}", request_id):
        env["PGOPTIONS"] = (
            env.get("PGOPTIONS", "") + f" -c throughline.actor={actor} -c throughline.request_id={request_id}"
        ).strip()
    if record["recoveries"]:
        record_output(
            conn,
            job_id,
            [
                "Resuming after worker interruption; finished steps are retained. An interrupted step may be attempted again."
            ],
        )
    owner = psutil.Process()
    proc = subprocess.Popen(
        [sys.executable, "-m", "throughline.jobs.processing_guard", name, str(owner.pid), str(owner.create_time())],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    output = queue.Queue(maxsize=1000)

    def read():
        try:
            while True:
                line = proc.stdout.readline(8192)
                if not line:
                    break
                output.put(line.rstrip("\n"))
        finally:
            output.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    end = False
    cancelled = False
    terminated_at = None
    try:
        while not end or proc.poll() is None:
            lines = []
            try:
                line = output.get(timeout=0.5)
                if line is None:
                    end = True
                else:
                    lines.append(line)
            except queue.Empty:
                pass
            while len(lines) < 500:
                try:
                    line = output.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    end = True
                    break
                lines.append(line)
            cancel = record_output(conn, job_id, lines)
            if cancel and not cancelled:
                cancelled = True
                terminated_at = time.monotonic()
                proc.terminate()
            if terminated_at and time.monotonic() - terminated_at > 7 and proc.poll() is None:
                proc.kill()
        code = proc.wait(timeout=3)
        one(
            conn,
            """UPDATE processing_runs SET state=%s,returncode=%s,finished_at=now(),heartbeat_at=now()
            WHERE id=%s""",
            ("stopped" if cancelled else "finished" if code == 0 else "failed", code, job_id),
        )
        conn.commit()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=7)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if proc.stdout:
            proc.stdout.close()


def run_worker():
    with database() as conn:
        one(conn, "SET statement_timeout='5s'")
        if not one(conn, "SELECT pg_try_advisory_lock(%s) AS owned", (WORKER_LOCK,))["owned"]:
            return
        conn.commit()
        while True:
            record = claim(conn)
            if record is None:
                return
            if not record:
                continue
            try:
                execute(conn, record)
            except OSError:
                one(
                    conn,
                    "UPDATE processing_runs SET state='failed',finished_at=now(),error='Worker could not launch registered command' WHERE id=%s",
                    (record["id"],),
                )
                conn.commit()


if __name__ == "__main__":
    try:
        run_worker()
    except Exception as exc:
        # Private exception values (including provider/DB diagnostics) stay out of logs.
        print(
            f"Throughline worker stopped ({type(exc).__name__}). Persisted work remains; check database availability and installed migrations.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
