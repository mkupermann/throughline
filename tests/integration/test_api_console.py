"""Console: the write barrier must be PostgreSQL's, not ours.

The bar for this phase is that a write attempt is *rejected by Postgres*. That
distinction matters: a keyword blocklist in Python is defeated by comments,
casing, and data-modifying CTEs, and it wrongly blocks a `SELECT` that merely
mentions the word "delete". These tests assert on the database's own error
text, so they fail if the guard is ever quietly downgraded to string matching.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration

#: Every shape a write can take, including the one a naive filter misses:
#: a DELETE hidden inside a CTE behind a leading SELECT.
WRITE_STATEMENTS = [
    "INSERT INTO memory_chunks (source_type, content, category) VALUES ('x','y','insight')",
    "UPDATE memory_chunks SET content = 'overwritten'",
    "DELETE FROM memory_chunks",
    "TRUNCATE messages",
    "CREATE TABLE evil (i int)",
    "DROP TABLE memory_chunks",
    "ALTER TABLE memory_chunks ADD COLUMN x int",
    "WITH d AS (DELETE FROM memory_chunks RETURNING *) SELECT count(*) FROM d",
    "CREATE INDEX evil_idx ON memory_chunks (id)",
]


@pytest.fixture()
def client(db_env):
    from throughline.api import deps

    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


@pytest.fixture()
def seeded(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO memory_chunks (source_type, content, category, confidence)
            SELECT 'manual', 'row ' || g, 'insight', 0.8 FROM generate_series(1, 25) g
            """)
    db_connection.commit()
    return db_connection


def _q(client, sql, **kw):
    return client.post("/api/console/query", json={"sql": sql, **kw}).json()


def test_select_works(client, seeded):
    body = _q(client, "SELECT count(*) AS n FROM memory_chunks")
    assert body["error"] is None
    assert body["columns"] == ["n"]
    assert body["rows"][0][0] == 25


@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_writes_are_rejected_by_postgres(client, seeded, statement):
    body = _q(client, statement)
    assert body["error"], f"{statement!r} was not rejected"
    assert "read-only transaction" in body["error"], (
        "the rejection must come from PostgreSQL's read-only transaction, not from "
        f"an application-level filter. Got: {body['error']!r}"
    )


def test_rejected_write_leaves_data_untouched(client, seeded, db_connection):
    _q(client, "DELETE FROM memory_chunks")
    _q(client, "UPDATE memory_chunks SET content = 'overwritten'")
    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM memory_chunks WHERE content LIKE 'row %'")
        assert cur.fetchone()[0] == 25, "a rejected write still changed data"


def test_select_mentioning_delete_is_not_blocked(client, seeded):
    """A keyword filter would wrongly reject this. Postgres does not."""
    body = _q(client, "SELECT 'delete from everything' AS drop_table_note")
    assert body["error"] is None
    assert body["rows"][0][0] == "delete from everything"


def test_row_cap_and_truncation_flag(client, seeded):
    body = _q(client, "SELECT * FROM memory_chunks", max_rows=10)
    assert body["row_count"] == 10
    assert body["truncated"] is True


def test_statement_timeout_is_enforced(client):
    body = _q(client, "SELECT pg_sleep(5)", timeout_ms=500)
    assert body["error"] and "cancelled" in body["error"].lower()
    assert body["error_hint"], "a timeout must suggest what to do about it"


def test_syntax_error_returns_200_with_message(client):
    r = client.post("/api/console/query", json={"sql": "SELEKT 1"})
    assert r.status_code == 200, "a SQL error is a normal console outcome, not an HTTP failure"
    assert r.json()["error"]


def test_empty_query_is_handled(client):
    assert _q(client, "   ")["error"] == "Enter a query."


def test_values_are_json_safe(client, seeded):
    body = _q(client, "SELECT created_at, tags, id FROM memory_chunks LIMIT 1")
    created, tags, ident = body["rows"][0]
    assert isinstance(created, str), "timestamps must serialise as ISO strings"
    assert isinstance(tags, list)
    assert isinstance(ident, int)


def test_schema_endpoint_lists_tables_and_snippets(client):
    body = client.get("/api/console/schema").json()
    names = {t["name"] for t in body["tables"]}
    assert {"memory_chunks", "conversations", "messages"} <= names
    assert body["snippets"] and all(s["sql"] for s in body["snippets"])
    assert any(e["name"] == "memory_category" for e in body["enums"])
