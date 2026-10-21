"""The PM capability fence: opt-in surface, inert when disabled.

Throughline's core promise is the knowledge pipeline. Project Management is a
second product bolted on; when ``THROUGHLINE_ENABLE_PM`` is off it must not
appear in routes, must not start the watch loop, and must not be advertised
to the SPA. These tests hold that line without a database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import _inject_meta, create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402


@pytest.fixture()
def client_factory(monkeypatch):
    """create_app without touching the pool or the pm watch loop."""
    from throughline.api import app as api_app

    monkeypatch.setattr(api_app, "init_pool", lambda _settings: None)
    monkeypatch.setattr(api_app, "close_pool", lambda: None)

    def make(enable_pm: bool, web_dist: Path | None = None) -> TestClient:
        app = create_app(Settings(web_dist=web_dist, enable_pm=enable_pm))
        return TestClient(app, raise_server_exceptions=False)

    return make


def test_pm_routes_absent_when_disabled(client_factory):
    with client_factory(enable_pm=False) as client:
        assert client.get("/api/pm/roles").status_code == 404
        assert client.get("/api/pm/overview").status_code == 404


def test_no_watch_loop_when_disabled(client_factory):
    with client_factory(enable_pm=False) as client:
        # TestClient's context manager runs lifespan. A watch task would be
        # on app.state; its absence is the assertion.
        assert getattr(client.app.state, "pm_watch_task", None) is None


def test_core_routes_still_registered_when_pm_disabled(client_factory):
    """The fence must not take the knowledge pipeline with it."""
    with client_factory(enable_pm=False) as client:
        # 503 = route exists, database is mocked away. 404 would mean the
        # fence over-reached and dropped a core router.
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/openapi.json").status_code == 200


def test_pm_routes_registered_when_enabled(client_factory):
    with client_factory(enable_pm=True) as client:
        # Route exists (not 404). A DB-less run may 500/503 — that is fine.
        assert client.get("/api/pm/roles").status_code != 404


def test_watch_loop_runs_when_enabled(client_factory):
    with client_factory(enable_pm=True) as client:
        task = client.app.state.pm_watch_task
        assert task is not None
        assert not task.done()


def test_inject_meta_reports_pm_off_by_default():
    html = _inject_meta("<html><head></head><body></body></html>", Settings(web_dist=None))
    assert "window.__THROUGHLINE_META__=" in html
    assert '"pm":false' in html


def test_inject_meta_reports_pm_when_enabled():
    html = _inject_meta(
        "<html><head></head></html>",
        Settings(web_dist=None, enable_pm=True),
    )
    assert '"pm":true' in html


def test_inject_meta_puts_script_before_head_close():
    html = _inject_meta("<html><head><title>t</title></head></html>", Settings(web_dist=None))
    assert html.index("__THROUGHLINE_META__") < html.index("</head>")


def test_spa_serves_injected_meta(tmp_path, client_factory):
    dist = tmp_path / "web"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")

    with client_factory(enable_pm=False, web_dist=dist) as client:
        response = client.get("/find")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '"pm":false' in response.text
