"""A pending migration must reach the worklist, and silence must mean silence.

Overview is the surface that claims to show only what needs attention. An
unapplied migration is the one fault that can damage the archive rather than
degrade a query: this database is the only surviving copy of most of what it
holds, because the source CLIs rotate their transcripts away. One such
migration sat pending behind a status line that reported the wrong table name.
"""

from __future__ import annotations

import psycopg2
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration

_RUNNER_DDL = """
CREATE TABLE IF NOT EXISTS applied_migrations (
    migration_name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
)
"""


@pytest.fixture()
def client(db_env):
    """Same shape as test_api_overview's fixture.

    The connection pool is process-global and outlives a per-test database, so
    it has to be dropped on both sides or a later test reuses a handle to a
    database that no longer exists.
    """
    from throughline.api import deps

    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


def _attention(payload) -> dict[str, dict]:
    assert "attention" in payload, f"overview did not return a worklist: {payload}"
    return {a["id"]: a for a in payload["attention"]}


def test_pending_migration_appears_as_critical(db_env, client):
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(_RUNNER_DDL)
            cur.execute("INSERT INTO applied_migrations (migration_name) VALUES ('000_baseline.sql')")
        conn.commit()
    finally:
        conn.close()

    payload = client.get("/api/overview").json()
    item = _attention(payload).get("pending-migrations")

    assert item is not None, "a pending migration did not reach the worklist"
    assert item["severity"] == "critical"
    assert item["count"] >= 1
    assert "migrate.py" in item["detail"], "the item must say how to fix it"
    assert payload["verdict"] in ("degraded", "broken")


def test_fully_migrated_database_says_nothing(db_env, client):
    """No false alarm once every migration in the repo is recorded."""
    import pathlib

    migrations = sorted(
        p.name for p in (pathlib.Path(__file__).resolve().parents[2] / "throughline" / "migrations").glob("*.sql")
    )
    conn = psycopg2.connect(**db_env)
    try:
        with conn.cursor() as cur:
            cur.execute(_RUNNER_DDL)
            for name in migrations:
                cur.execute(
                    "INSERT INTO applied_migrations (migration_name) VALUES (%s) ON CONFLICT DO NOTHING",
                    (name,),
                )
        conn.commit()
    finally:
        conn.close()

    payload = client.get("/api/overview").json()
    assert "pending-migrations" not in _attention(payload)


def test_untracked_database_does_not_cry_wolf(db_env, client):
    """With no tracking table the answer is unknown, not "everything is pending".

    Reporting every migration as pending on a database created straight from
    schema.sql would make the loudest item on the worklist the one most likely
    to be wrong.
    """
    payload = client.get("/api/overview").json()
    assert "pending-migrations" not in _attention(payload)
