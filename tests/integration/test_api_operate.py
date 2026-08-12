"""Operate: pipeline status, the job runner, and SSE streaming."""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api import jobs as jobs_mod  # noqa: E402
from throughline.api.app import create_app  # noqa: E402
from throughline.api.jobs import JobRunner, JobSpec  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env, monkeypatch):
    from throughline import embedding
    from throughline.api import deps

    monkeypatch.setattr(
        embedding, "backend_info",
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
        "echo": JobSpec("echo", "Echo", "prints lines",
                        [sys.executable, "-c", "print('one'); print('two')"]),
        "fail": JobSpec("fail", "Fail", "exits non-zero",
                        [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"]),
        "slow": JobSpec("slow", "Slow", "sleeps",
                        [sys.executable, "-c",
                         "import time,sys\n"
                         "for i in range(20):\n"
                         "    print(i, flush=True); time.sleep(0.3)"]),
        "missing": JobSpec("missing", "Missing", "binary does not exist",
                           ["/nonexistent/binary-xyz"]),
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
        "counts", "database", "extensions", "embedding", "pending", "ingestion", "jobs", "history",
    }
    assert isinstance(body["database"]["reachable"], bool)
    assert isinstance(body["extensions"]["pgvector_usable"], bool)
    assert body["database"]["dbname"], "the panel must name the database it is talking to"
    assert "password" not in body["database"], "credentials must never be serialised"


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
    spec = JobSpec("chatty", "Chatty", "many lines",
                   [sys.executable, "-c", "for i in range(500): print(i)"])
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
        llm, "backend_info",
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
        llm, "backend_info",
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
        llm, "backend_info",
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
        embedding, "backend_info",
        lambda preferred="auto": embedding.BackendInfo(
            available=False, reason="Ollama is not running."),
    )
    jobs = {j["name"]: j for j in client.get("/api/operate/status").json()["jobs"]}
    assert jobs["embed"]["unavailable"] == "Ollama is not running."
