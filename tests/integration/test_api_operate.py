"""Operate: pipeline status, the job runner, and SSE streaming."""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline import queries as Q  # noqa: E402
from throughline.api import jobs as jobs_mod  # noqa: E402
from throughline.api.app import create_app  # noqa: E402
from throughline.api.jobs import JobRunner, JobSpec  # noqa: E402
from throughline.api.routers.operate import derive_pipeline_stages  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env, monkeypatch):
    from throughline import embedding
    from throughline.api import deps

    monkeypatch.setattr(
        embedding,
        "backend_info",
        lambda preferred="auto": embedding.BackendInfo(available=False, reason="none"),
    )
    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


@pytest.fixture()
def fake_jobs(monkeypatch):
    """Replace the real pipeline commands with fast, deterministic ones.

    Running the actual ingest/extract jobs in a test would be slow, would need
    an LLM, and would test those scripts rather than the runner.
    """
    import sys

    specs = {
        "echo": JobSpec("echo", "Echo", "prints lines", [sys.executable, "-c", "print('one'); print('two')"]),
        "fail": JobSpec(
            "fail", "Fail", "exits non-zero", [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"]
        ),
        "slow": JobSpec(
            "slow",
            "Slow",
            "sleeps",
            [sys.executable, "-c", "import time,sys\nfor i in range(20):\n    print(i, flush=True); time.sleep(0.3)"],
        ),
        "missing": JobSpec("missing", "Missing", "binary does not exist", ["/nonexistent/binary-xyz"]),
    }
    monkeypatch.setattr(jobs_mod, "JOBS", specs)
    monkeypatch.setattr("throughline.api.routers.operate.JOBS", specs)
    runner = JobRunner()
    monkeypatch.setattr(jobs_mod, "runner", runner)
    monkeypatch.setattr("throughline.api.routers.operate.runner", runner)
    return runner


def _wait(job, timeout=15):
    deadline = time.time() + timeout
    while job.running and time.time() < deadline:
        time.sleep(0.05)
    return job


def test_status_shape(client):
    body = client.get("/api/operate/status").json()
    assert set(body) >= {
        "counts",
        "database",
        "extensions",
        "embedding",
        "pending",
        "ingestion",
        "jobs",
        "history",
        "pipeline",
        "providers",
    }
    assert isinstance(body["database"]["reachable"], bool)
    assert isinstance(body["extensions"]["pgvector_usable"], bool)
    assert body["database"]["dbname"], "the panel must name the database it is talking to"
    assert "password" not in body["database"], "credentials must never be serialised"
    assert [stage["key"] for stage in body["pipeline"]] == [
        "discover",
        "ingest",
        "extract",
        "embed",
        "review",
    ]


def _pipeline(**overrides):
    values = {
        "provider_coverage": [
            {
                "name": "claude_code",
                "label": "Claude Code",
                "on_disk": 4,
                "pending": 0,
                "status": "ok",
            }
        ],
        "pending": {"extraction": 0, "titles": 0},
        "embedding_coverage": {"total": 3, "embedded": 3},
        "vector_ok": True,
        "jobs": [
            {
                "name": name,
                "running": False,
                "job_id": None,
                "unavailable": None,
            }
            for name in ("ingest", "extract", "embed", "audit-extraction")
        ],
        "history": [],
        "snapshot": {
            "captured_at": "2026-09-03T10:00:00+00:00",
            "last_extraction_at": "2026-09-02T10:00:00+00:00",
            "last_audit_at": "2026-09-03T09:00:00+00:00",
            "last_audit_sampled": 20,
            "last_audit_drifted": 0,
        },
        "last_ingestion_at": "2026-09-03T08:00:00+00:00",
        "last_embedding_at": "2026-09-03T08:30:00+00:00",
        "audit_findings_available": True,
        "drift_findings": 0,
    }
    values.update(overrides)
    return derive_pipeline_stages(**values)


def test_pipeline_derives_healthy_and_due_states_in_workflow_order():
    providers = [
        {
            "name": "claude_code",
            "label": "Claude Code",
            "on_disk": 4,
            "pending": 2,
            "status": "pending",
        }
    ]
    stages = _pipeline(
        provider_coverage=providers,
        pending={"extraction": 3, "titles": 0},
        embedding_coverage={"total": 5, "embedded": 4},
        snapshot={
            "captured_at": "2026-09-03T10:00:00+00:00",
            "last_extraction_at": None,
            "last_audit_at": None,
            "last_audit_sampled": 0,
            "last_audit_drifted": 0,
        },
    )

    assert [stage["key"] for stage in stages] == [
        "discover",
        "ingest",
        "extract",
        "embed",
        "review",
    ]
    assert [stage["state"] for stage in stages] == [
        "healthy",
        "due",
        "due",
        "due",
        "due",
    ]
    assert stages[1]["last_success"] == "2026-09-03T08:00:00+00:00"


def test_pipeline_running_failed_and_blocked_states_take_precedence():
    jobs = [
        {"name": "ingest", "running": True, "job_id": "run-1", "unavailable": None},
        {"name": "extract", "running": False, "job_id": None, "unavailable": None},
        {
            "name": "embed",
            "running": False,
            "job_id": None,
            "unavailable": "Ollama is not running.",
        },
        {
            "name": "audit-extraction",
            "running": False,
            "job_id": None,
            "unavailable": None,
        },
    ]
    history = [
        {
            "name": "extract",
            "running": False,
            "returncode": 2,
            "error": None,
            "finished_at": 1788426000.0,
        }
    ]
    by_key = {stage["key"]: stage for stage in _pipeline(jobs=jobs, history=history)}

    assert by_key["ingest"]["state"] == "running"
    assert by_key["ingest"]["job_id"] == "run-1"
    assert by_key["extract"]["state"] == "failed"
    assert "code 2" in by_key["extract"]["blocked_reason"]
    assert by_key["embed"]["state"] == "blocked"
    assert by_key["embed"]["blocked_reason"] == "Ollama is not running."


def test_pipeline_ignores_a_stale_web_failure_after_a_newer_persisted_success():
    history = [
        {
            "name": "ingest",
            "running": False,
            "returncode": 2,
            "finished_at": 1_000.0,
        }
    ]

    ingest = {
        stage["key"]: stage
        for stage in _pipeline(
            history=history,
            last_ingestion_at="1970-01-01T00:20:00+00:00",
        )
    }["ingest"]

    assert ingest["state"] == "healthy"
    assert ingest["last_success"] == "1970-01-01T00:20:00+00:00"


def test_pipeline_exposes_the_actual_running_provider_job():
    jobs = [
        {"name": "ingest", "running": False, "job_id": None, "unavailable": None},
        {"name": "ingest_hermes", "running": True, "job_id": "provider-1", "unavailable": None},
        {"name": "extract", "running": False, "job_id": None, "unavailable": None},
        {"name": "embed", "running": False, "job_id": None, "unavailable": None},
        {"name": "audit-extraction", "running": False, "job_id": None, "unavailable": None},
    ]

    ingest = {stage["key"]: stage for stage in _pipeline(jobs=jobs)}["ingest"]

    assert ingest["state"] == "running"
    assert ingest["job_id"] == "provider-1"
    assert ingest["job_name"] == "ingest_hermes"


def test_pipeline_turns_drift_findings_into_a_review_action():
    snapshot = {
        "captured_at": "2026-09-03T10:00:00+00:00",
        "last_extraction_at": "2026-09-02T10:00:00+00:00",
        "last_audit_at": "2026-09-03T09:00:00+00:00",
        "last_audit_sampled": 20,
        "last_audit_drifted": 2,
    }
    review = {stage["key"]: stage for stage in _pipeline(snapshot=snapshot, drift_findings=2)}["review"]

    assert review["state"] == "due"
    assert review["action_href"] == "/curate?queue=drift"
    assert review["action_label"] == "Review findings"


def test_pipeline_does_not_link_legacy_audit_counts_to_an_empty_queue():
    snapshot = {
        "captured_at": "2026-09-03T10:00:00+00:00",
        "last_extraction_at": "2026-09-02T10:00:00+00:00",
        "last_audit_at": "2026-09-03T09:00:00+00:00",
        "last_audit_sampled": 20,
        "last_audit_drifted": 2,
    }

    review = {
        stage["key"]: stage
        for stage in _pipeline(
            snapshot=snapshot,
            audit_findings_available=False,
            drift_findings=0,
        )
    }["review"]

    assert review["state"] == "due"
    assert review["action_href"] is None
    assert review["action_label"] == "Run drift audit"
    assert "current audit" in review["detail"].lower()


def test_pipeline_marks_resolved_drift_findings_current():
    snapshot = {
        "captured_at": "2026-09-03T10:00:00+00:00",
        "last_extraction_at": "2026-09-02T10:00:00+00:00",
        "last_audit_at": "2026-09-03T09:00:00+00:00",
        "last_audit_sampled": 20,
        "last_audit_drifted": 2,
    }

    review = {
        stage["key"]: stage
        for stage in _pipeline(
            snapshot=snapshot,
            audit_findings_available=True,
            drift_findings=0,
        )
    }["review"]

    assert review["state"] == "healthy"
    assert "resolved" in review["detail"].lower()


def test_status_scopes_embedding_state_to_the_active_backend(client, monkeypatch):
    from throughline import embedding

    seen = {}

    monkeypatch.setattr(
        embedding,
        "backend_info",
        lambda preferred="auto": embedding.BackendInfo(
            available=True,
            name="ollama",
            model="active-model",
            column="embedding_768",
            dim=768,
        ),
    )

    def coverage(conn, model, column):
        seen["coverage"] = (model, column)
        return {"total": 2, "embedded": 1}

    def last_embedding(conn, model, column):
        seen["last"] = (model, column)
        return None

    monkeypatch.setattr(Q.health, "embedding_coverage", coverage)
    monkeypatch.setattr(Q.health, "last_embedding_at", last_embedding)

    response = client.get("/api/operate/status")

    assert response.status_code == 200
    assert seen == {
        "coverage": ("active-model", "embedding_768"),
        "last": ("active-model", "embedding_768"),
    }


def test_embedding_health_queries_require_the_active_model_and_vector_column(db_connection):
    with db_connection.cursor() as cur:
        chunk_ids = []
        for label in ("usable", "wrong model", "wrong column"):
            cur.execute(
                "INSERT INTO memory_chunks (source_type, content, category) "
                "VALUES ('manual', %s, 'insight') RETURNING id",
                (label,),
            )
            chunk_ids.append(cur.fetchone()[0])
        cur.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, model, embedding_768, created_at) "
            "VALUES ('memory_chunk', %s, 'active-model', "
            "array_fill(0.1::real, ARRAY[768])::vector, '2026-09-01T10:00:00+00:00')",
            (chunk_ids[0],),
        )
        cur.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, model, embedding_768, created_at) "
            "VALUES ('memory_chunk', %s, 'old-model', "
            "array_fill(0.1::real, ARRAY[768])::vector, '2026-09-02T10:00:00+00:00')",
            (chunk_ids[1],),
        )
        cur.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, model, embedding_1536, created_at) "
            "VALUES ('memory_chunk', %s, 'active-model', "
            "array_fill(0.1::real, ARRAY[1536])::vector, '2026-09-03T10:00:00+00:00')",
            (chunk_ids[2],),
        )
    db_connection.commit()

    coverage = Q.health.embedding_coverage(
        db_connection,
        model="active-model",
        column="embedding_768",
    )
    last = Q.health.last_embedding_at(
        db_connection,
        model="active-model",
        column="embedding_768",
    )

    assert coverage == {"total": 3, "embedded": 1}
    assert last.isoformat() == "2026-09-01T10:00:00+00:00"


def test_run_unknown_job_404s(client, fake_jobs):
    assert client.post("/api/operate/run/nope").status_code == 404


def test_job_runs_and_captures_output(client, fake_jobs):
    res = client.post("/api/operate/run/echo").json()
    job = _wait(fake_jobs.get(res["job_id"]))
    snap = client.get(f"/api/operate/job/{res['job_id']}").json()
    assert snap["returncode"] == 0
    assert "one" in snap["lines"] and "two" in snap["lines"]
    assert not job.running


def test_failing_job_reports_its_exit_code(client, fake_jobs):
    res = client.post("/api/operate/run/fail").json()
    _wait(fake_jobs.get(res["job_id"]))
    snap = client.get(f"/api/operate/job/{res['job_id']}").json()
    assert snap["returncode"] == 3, "a failed job must not look like a successful one"


def test_missing_binary_is_reported_not_crashed(client, fake_jobs):
    res = client.post("/api/operate/run/missing").json()
    _wait(fake_jobs.get(res["job_id"]))
    snap = client.get(f"/api/operate/job/{res['job_id']}").json()
    assert snap["error"], "a job that cannot start must record why"
    assert not snap["running"]


def test_same_job_does_not_start_twice(client, fake_jobs):
    a = client.post("/api/operate/run/slow").json()
    b = client.post("/api/operate/run/slow").json()
    assert a["job_id"] == b["job_id"], "a second run must attach to the running job"
    fake_jobs.stop(a["job_id"])


def test_stream_replays_then_completes(client, fake_jobs):
    res = client.post("/api/operate/run/echo").json()
    events = []
    with client.stream("GET", f"/api/operate/job/{res['job_id']}/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for raw in r.iter_lines():
            events.append(raw)
            if raw.startswith("event: done"):
                break
    text = "\n".join(events)
    assert "one" in text and "two" in text, "the stream must replay buffered output"
    assert "event: done" in text


def test_stream_of_unknown_job_404s(client, fake_jobs):
    assert client.get("/api/operate/job/deadbeef/stream").status_code == 404


def test_stop_a_running_job(client, fake_jobs):
    res = client.post("/api/operate/run/slow").json()
    time.sleep(0.4)
    assert client.post(f"/api/operate/stop/{res['job_id']}").status_code == 200
    _wait(fake_jobs.get(res["job_id"]))
    assert not fake_jobs.get(res["job_id"]).running


def test_stop_unknown_job_404s(client, fake_jobs):
    assert client.post("/api/operate/stop/nope").status_code == 404


def test_history_records_finished_runs(client, fake_jobs):
    res = client.post("/api/operate/run/echo").json()
    _wait(fake_jobs.get(res["job_id"]))
    history = client.get("/api/operate/status").json()["history"]
    assert any(h["id"] == res["job_id"] for h in history)
    assert all("lines" not in h for h in history), "history is a summary, not a log dump"


def test_output_buffer_is_bounded(monkeypatch):
    """A chatty job must not grow memory without bound."""
    import sys

    monkeypatch.setattr(jobs_mod, "MAX_LINES", 20)
    runner = JobRunner()
    spec = JobSpec("chatty", "Chatty", "many lines", [sys.executable, "-c", "for i in range(500): print(i)"])
    monkeypatch.setattr(jobs_mod, "JOBS", {"chatty": spec})
    job = runner.start("chatty")
    _wait(job)
    snap = job.snapshot()
    assert len(snap["lines"]) <= 20
    assert snap["dropped_lines"] > 0, "dropped output must be reported, not silently lost"


# ── Environment prerequisites ────────────────────────────────────────────────


def _no_model(monkeypatch, detail="No model available. Start Ollama or set OPENAI_API_KEY."):
    """Make every answering backend look unreachable.

    Extraction, titling and reflection used to require the Claude CLI
    specifically; they now take any backend `throughline.llm` can find, so the
    prerequisite to stub is the probe, not one vendor's binary.
    """
    from throughline import llm

    monkeypatch.setattr(
        llm,
        "backend_info",
        lambda: llm.LLMInfo(available=False, detail=detail),
    )
    return detail


def test_unavailable_job_is_reported_before_it_is_offered(client, monkeypatch):
    """A job that cannot run must say so in /status, not fail after a click."""
    detail = _no_model(monkeypatch)
    jobs = {j["name"]: j for j in client.get("/api/operate/status").json()["jobs"]}
    assert jobs["extract"]["unavailable"], "extract needs a model and should say so"
    assert detail in jobs["extract"]["unavailable"]
    # A job with no prerequisites stays runnable.
    assert jobs["doctor"]["unavailable"] is None


def test_available_job_reports_no_obstacle(client, monkeypatch):
    from throughline import llm

    monkeypatch.setattr(
        llm,
        "backend_info",
        lambda: llm.LLMInfo(available=True, backend="ollama", model="qwen2.5:7b", local=True),
    )
    jobs = {j["name"]: j for j in client.get("/api/operate/status").json()["jobs"]}
    assert jobs["extract"]["unavailable"] is None


def test_a_local_model_makes_the_container_jobs_runnable(client, monkeypatch):
    """The point of the port: these three used to be dead inside Docker.

    The Claude CLI carries host credentials and is deliberately not in the
    image, so extraction, titles and reflection could never run there. With a
    local backend they can.
    """
    from throughline import llm

    monkeypatch.setattr(
        llm,
        "backend_info",
        lambda: llm.LLMInfo(available=True, backend="ollama", model="qwen2.5:7b", local=True),
    )
    jobs = {j["name"]: j for j in client.get("/api/operate/status").json()["jobs"]}
    for name in ("extract", "titles", "reflect"):
        assert jobs[name]["unavailable"] is None, f"{name} should run on a local model"


def test_starting_an_unavailable_job_is_refused(client, monkeypatch):
    """Direct POST must not spawn a process guaranteed to fail."""
    detail = _no_model(monkeypatch)
    r = client.post("/api/operate/run/extract")
    assert r.status_code == 409, "a well-formed request the environment cannot serve is a 409"
    assert detail in r.json()["detail"]


def test_embedding_job_reports_the_backend_reason(client, monkeypatch):
    from throughline import embedding

    monkeypatch.setattr(
        embedding,
        "backend_info",
        lambda preferred="auto": embedding.BackendInfo(available=False, reason="Ollama is not running."),
    )
    jobs = {j["name"]: j for j in client.get("/api/operate/status").json()["jobs"]}
    assert jobs["embed"]["unavailable"] == "Ollama is not running."
