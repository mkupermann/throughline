"""The /api/export surface.

The service has no authentication. Everything else it exposes is read-only,
so a destination the caller chooses is the one place where a request can
change the filesystem — these tests are what keeps that bounded.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.routers import export as export_router  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("THROUGHLINE_EXPORT_ROOT", str(tmp_path))
    app = fastapi.FastAPI()
    app.include_router(export_router.router, prefix="/api")
    return TestClient(app)


def test_the_default_destination_is_offered_but_nothing_is_written(client, tmp_path):
    body = client.get("/api/export/markdown").json()
    assert body["root"] == str(tmp_path)
    assert body["suggested"].startswith(str(tmp_path))
    assert not any(tmp_path.iterdir())


def test_a_destination_outside_the_root_is_refused_with_a_reason(client):
    r = client.post("/api/export/markdown", json={"out": "/etc/throughline"})
    assert r.status_code == 400
    assert "outside" in r.json()["detail"]


def test_an_empty_destination_is_refused(client):
    r = client.post("/api/export/markdown", json={"out": "  "})
    assert r.status_code == 400
    assert "No destination" in r.json()["detail"]


def test_a_relative_destination_is_refused(client):
    r = client.post("/api/export/markdown", json={"out": "vault"})
    assert r.status_code == 400
    assert "absolute path" in r.json()["detail"]


def test_an_accepted_destination_starts_the_job_and_never_reaches_argv(client, tmp_path, monkeypatch):
    started = {}

    def fake_start(name, extra_env=None):
        started["name"] = name
        started["env"] = extra_env or {}
        return type("J", (), {"id": "abc123", "name": name, "running": True})()

    monkeypatch.setattr(export_router.runner, "start", fake_start)

    target = tmp_path / "Obsidian" / "Throughline"
    r = client.post("/api/export/markdown", json={"out": str(target), "redact": True, "toolOutput": 400})

    assert r.status_code == 200
    assert r.json()["job"]["id"] == "abc123"
    assert r.json()["out"] == str(target)
    assert started["name"] == "export-markdown"
    # The destination travels in the environment, not on the command line.
    assert started["env"]["THROUGHLINE_EXPORT_OUT"] == str(target)
    assert started["env"]["THROUGHLINE_EXPORT_REDACT"] == "1"
    assert started["env"]["THROUGHLINE_EXPORT_TOOL_OUTPUT"] == "400"


def test_options_left_out_do_not_appear_in_the_environment(client, tmp_path, monkeypatch):
    started = {}
    monkeypatch.setattr(
        export_router.runner,
        "start",
        lambda name, extra_env=None: (
            started.update(env=extra_env or {}),
            type("J", (), {"id": "x", "name": name, "running": True})(),
        )[1],
    )
    client.post("/api/export/markdown", json={"out": str(tmp_path / "v")})
    assert "THROUGHLINE_EXPORT_REDACT" not in started["env"]


def test_the_endpoint_is_reachable_on_the_assembled_app(tmp_path, monkeypatch):
    # A router nobody included is a feature nobody has.
    monkeypatch.setenv("THROUGHLINE_EXPORT_ROOT", str(tmp_path))
    from throughline.api.app import create_app

    response = TestClient(create_app()).get("/api/export/markdown")
    assert response.status_code == 200
    assert response.json()["root"] == str(tmp_path)


def test_the_export_job_is_not_offered_as_a_one_click_run(tmp_path, monkeypatch):
    # It needs a destination. A Run button that cannot work is the same
    # mistake as a search that silently returns less than it should.
    monkeypatch.setenv("THROUGHLINE_EXPORT_ROOT", str(tmp_path))
    from throughline.api.jobs import JOBS
    from throughline.api.routers.operate import HIDDEN_JOBS

    assert "export-markdown" in JOBS
    assert "export-markdown" in HIDDEN_JOBS


def test_no_job_requires_a_vendor_cli_any_more():
    # The "claude" requirement existed so the UI could grey out jobs that
    # needed the CLI. Nothing needs it now, and leaving the branch means the
    # next job to be added can quietly reintroduce the dependency.
    from throughline.api.jobs import JOBS, Requirement

    assert "claude" not in getattr(Requirement, "__args__", ())
    assert all(spec.requires != "claude" for spec in JOBS.values())


# --------------------------------------------------------------------------- #
# What the Operate page can see                                               #
# --------------------------------------------------------------------------- #


def test_operate_status_reports_the_generation_backend(monkeypatch):
    # The page shows which model embeds but not which one generates, so the
    # answer to "what is extracting my memory, and does it leave the machine?"
    # was only available from the command line.
    from throughline import llm
    from throughline.api.routers import operate

    monkeypatch.setattr(
        llm,
        "backend_info",
        lambda: llm.LLMInfo(True, backend="ollama", model="qwen3.5:9b", local=True, detail="qwen3.5:9b"),
    )
    payload = operate.generation_panel()

    assert payload["backend"] == "ollama"
    assert payload["model"] == "qwen3.5:9b"
    assert payload["local"] is True
    assert payload["available"] is True


def test_an_unavailable_generation_backend_says_why(monkeypatch):
    from throughline import llm
    from throughline.api.routers import operate

    monkeypatch.setattr(llm, "backend_info", lambda: llm.LLMInfo(False, detail="No model available."))
    payload = operate.generation_panel()

    assert payload["available"] is False
    assert payload["detail"] == "No model available."
