"""Restart, cancellation, exclusivity and trusted checkpoint proof with real PostgreSQL."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from throughline.api.durable_jobs import WORKER_LOCK, DurableJobRunner, database
from throughline.api.jobs import JobUnavailable
from throughline.jobs import process_all, processing_worker
from throughline.queries._exec import one

pytestmark = pytest.mark.integration


@pytest.fixture
def runner(db_env, monkeypatch):
    monkeypatch.setattr(DurableJobRunner, "kick", lambda self: None)
    return DurableJobRunner()


def test_queue_survives_new_runner_and_concurrent_submission(runner):
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: DurableJobRunner().start("doctor").id, range(2)))
    assert ids[0] == ids[1]
    restored = DurableJobRunner().get(ids[0])
    assert restored.snapshot()["state"] == "queued"
    assert restored.running
    assert DurableJobRunner().stop(ids[0])
    assert not restored.running
    assert restored.snapshot()["state"] == "stopped"


def test_full_pass_blocks_new_overlaps_and_keeps_existing_queue_order(runner):
    first = runner.start("doctor")
    complete = runner.start("process-all")
    with pytest.raises(JobUnavailable):
        runner.start("scan-prompts")
    with database() as conn:
        record = processing_worker.claim(conn)
        assert record["id"] == first.id
    assert runner.current("process-all").id == complete.id
    assert runner.stop(complete.id)
    assert DurableJobRunner().get(complete.id).snapshot()["stop_requested"]


def test_model_stdout_cannot_forge_completed_stages_and_checkpoint_survives_restart(runner, monkeypatch):
    job = runner.start("process-all")
    with database() as conn:
        processing_worker.claim(conn)
        processing_worker.record_output(conn, job.id, ['::stage {"name":"embed","state":"finished"}'])
    assert runner.get(job.id).snapshot()["stages"] == {}
    monkeypatch.setenv("THROUGHLINE_PROCESSING_RUN_ID", job.id)
    process_all.status("ingest", "finished")
    with database() as conn:
        one(conn, "UPDATE processing_runs SET heartbeat_at=now()-interval '1 minute' WHERE id=%s", (job.id,))
    with database() as conn:
        resumed = processing_worker.claim(conn)
        assert resumed["recoveries"] == 1
        assert resumed["stages"]["ingest"]["state"] == "finished"
        assert "embed" not in resumed["stages"]
    monkeypatch.setenv("THROUGHLINE_RESUME_STAGES", json.dumps(list(process_all.STEPS)))
    monkeypatch.setattr(process_all.subprocess, "run", lambda *a, **k: pytest.fail("Completed step executed again"))
    process_all.main()
    assert len(DurableJobRunner().get(job.id).snapshot()["stages"]) == 11


def test_a_second_worker_cannot_claim_while_the_owner_holds_the_lock(runner, monkeypatch):
    runner.start("doctor")
    with database() as conn:
        assert one(conn, "SELECT pg_try_advisory_lock(%s) AS owned", (WORKER_LOCK,))["owned"]
        monkeypatch.setattr(processing_worker, "claim", lambda *a: pytest.fail("A second worker entered the queue"))
        processing_worker.run_worker()


def test_no_credentials_or_arbitrary_environment_can_be_persisted(runner):
    with pytest.raises(ValueError):
        runner.start("doctor", extra_env={"OPENAI_API_KEY": "do-not-store"})
    with pytest.raises(ValueError):
        runner.start("export-markdown", extra_env={"PATH": "/malicious"})


def test_real_worker_process_recovers_without_repeating_finished_steps(runner):
    import os
    import subprocess
    import sys

    from psycopg2.extras import Json

    job = runner.start("process-all")
    with database() as conn:
        one(
            conn,
            "UPDATE processing_runs SET state='running',heartbeat_at=now()-interval '1 minute',stages=%s WHERE id=%s",
            (Json({step: {"state": "finished"} for step in process_all.STEPS}), job.id),
        )
    result = subprocess.run(
        [sys.executable, "-m", "throughline.jobs.processing_worker"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    restored = DurableJobRunner().get(job.id).snapshot()
    assert restored["state"] == "finished"
    assert restored["returncode"] == 0
    assert restored["recoveries"] == 1
    assert len(restored["stages"]) == 11
    assert any("Resuming after worker interruption" in line for line in restored["lines"])


def test_stream_keeps_new_output_when_the_bounded_tail_rotates(runner):
    job = runner.start("doctor")
    with database() as conn:
        processing_worker.record_output(conn, job.id, [str(i) for i in range(500)])
    stream = runner.stream(job, poll=0)
    initial = [next(stream) for _ in range(500)]
    assert "499" in initial[-1]
    assert next(stream).startswith(": keepalive")
    with database() as conn:
        processing_worker.record_output(conn, job.id, ["new line"])
    assert "new line" in next(stream)
    assert next(stream).startswith(": keepalive")
    stream.close()


def test_lifecycle_changes_are_audited_without_diagnostic_contents(runner):
    from throughline.api.access import actor_context

    token = actor_context.set(("user:123", "a" * 32))
    try:
        job = runner.start("doctor")
    finally:
        actor_context.reset(token)
    with database() as conn:
        processing_worker.claim(conn)
        processing_worker.record_output(conn, job.id, ["fictional diagnostic contents"])
    token = actor_context.set(("user:456", "b" * 32))
    try:
        runner.stop(job.id)
    finally:
        actor_context.reset(token)
    with database() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT actor,action,changed_fields FROM access_audit WHERE entity_type='processing_runs' AND entity_id=%s ORDER BY id",
                (job.id,),
            )
            events = cursor.fetchall()
    assert len(events) == 3
    assert [event[0] for event in events] == ["user:123", "user:123", "user:456"]
    assert events[0][1] == "PROCESSING_QUEUED"
    assert events[1][1] == "PROCESSING_RUNNING"
    assert "stop_requested" in events[2][2]
    assert "fictional diagnostic contents" not in str(events)


def test_repeated_worker_crashes_stop_automatic_retries(runner):
    job = runner.start("process-all")
    with database() as conn:
        one(
            conn,
            "UPDATE processing_runs SET state='running',recoveries=3,heartbeat_at=now()-interval '1 minute' WHERE id=%s",
            (job.id,),
        )
    with database() as conn:
        assert processing_worker.claim(conn) == {}
    result = runner.get(job.id).snapshot()
    assert result["state"] == "failed"
    assert result["recoveries"] == 3
    assert "interrupted repeatedly" in result["error"]
