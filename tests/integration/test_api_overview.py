"""API contract and behaviour tests for the /overview surface.

Overview is a worklist, so the assertions are about *what surfaces when* —
an item that fires on a healthy database, or stays silent on a broken one,
is the bug worth catching.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env, monkeypatch):
    """A TestClient pointed at the throwaway integration database.

    `db_env` sets the PG* variables the pool reads, so the app connects to
    the per-test database rather than the developer's real one.
    """
    from throughline.api import deps

    deps.close_pool()
    app = create_app(Settings(web_dist=None))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


@pytest.fixture()
def seeded(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, model, entrypoint, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/repo/alpha', 'claude', 'claude-code',
                    now() - interval '1 day', 9, 'seed')
            RETURNING id
            """
        )
        conv = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, confidence, project_name, status)
            VALUES ('conversation', %s, 'a high confidence decision', 'decision', 0.95, 'alpha', 'active'),
                   ('conversation', %s, 'a shaky insight', 'insight', 0.30, 'alpha', 'active')
            """,
            (conv, conv),
        )
        cur.execute(
            "INSERT INTO ingestion_log (file_path, file_hash, record_count) "
            "VALUES ('/tmp/seed.jsonl', 'abc', 1)"
        )
    db_connection.commit()
    return db_connection


def test_health_does_not_require_the_database(client):
    """Liveness must not depend on Postgres — see the docstring in app.py."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_overview_shape(client, seeded):
    r = client.get("/api/overview")
    assert r.status_code == 200
    body = r.json()

    assert set(body) == {"headline", "verdict", "verdict_reason", "attention", "activity", "totals"}
    assert body["verdict"] in {"ok", "degraded", "broken"}
    assert set(body["headline"]) == {"label", "value", "sublabel"}
    for item in body["attention"]:
        assert set(item) == {
            "id", "severity", "title", "detail", "count", "action", "action_label",
        }
        assert item["severity"] in {"critical", "warning", "info"}


def test_low_confidence_chunk_surfaces_as_attention(client, seeded):
    body = client.get("/api/overview").json()
    ids = {a["id"] for a in body["attention"]}
    assert "low-confidence" in ids, f"expected a low-confidence item, got {ids}"
    item = next(a for a in body["attention"] if a["id"] == "low-confidence")
    assert item["count"] == 1
    assert item["action"], "an attention item must route somewhere actionable"


def test_verdict_degrades_when_items_exist(client, seeded):
    body = client.get("/api/overview").json()
    if body["attention"]:
        assert body["verdict"] in {"degraded", "broken"}
        assert body["verdict_reason"] != "Everything looks healthy."
    else:
        assert body["verdict"] == "ok"


def test_verdict_reason_pluralises(client, seeded):
    body = client.get("/api/overview").json()
    n = len(body["attention"])
    if n == 1:
        assert "1 item needs attention" in body["verdict_reason"]
    elif n > 1:
        assert f"{n} items need attention" in body["verdict_reason"]


def test_totals_are_integers(client, seeded):
    totals = client.get("/api/overview").json()["totals"]
    assert totals["conversations"] >= 1
    assert all(isinstance(v, int) for v in totals.values())


def test_activity_is_json_safe(client, seeded):
    """Dates must serialise as strings, not leak a Python date object."""
    for point in client.get("/api/overview").json()["activity"]:
        assert isinstance(point["day"], str)
        assert isinstance(point["n"], int)


def test_unknown_api_route_is_404_not_spa(client):
    """/api/* must never fall through to the SPA handler."""
    assert client.get("/api/does-not-exist").status_code == 404
