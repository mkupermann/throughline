"""Curate: queues, reversible mutations, undo tokens, idempotency.

The property that matters is that "forget" is genuinely undoable *and*
genuinely hides the chunk. Either half alone is a bug: an undo that cannot
restore is a lie, and a forget that leaves the memory findable everywhere else
is worse than not offering the action.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402
from throughline.api.undo import UndoRegistry, registry  # noqa: E402

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
    registry.clear()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()
    registry.clear()


@pytest.fixture()
def chunks(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO conversations (session_id, project_path, started_at, message_count)
            VALUES (gen_random_uuid(), '/repo/alpha', now(), 1) RETURNING id
            """)
        conv = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, confidence, project_name, status,
                 access_count, expires_at, created_at)
            VALUES
                ('conversation', %s, 'a distinctive zorblat decision', 'decision', 0.30, 'alpha', 'active', 0,
                 NULL, now() - interval '90 days'),
                ('conversation', %s, 'another zorblat note', 'insight', 0.95, 'alpha', 'active', 5,
                 now() + interval '10 days', now())
            RETURNING id
            """,
            (conv, conv),
        )
        ids = [r[0] for r in cur.fetchall()]
    db_connection.commit()
    return ids


def _findable(client, chunk_id: int) -> bool:
    body = client.get("/api/find", params={"q": "zorblat", "kind": "memory", "limit": 100}).json()
    return any(i["id"] == chunk_id for i in body["items"])


def test_queues_listed_with_counts(client, chunks):
    body = client.get("/api/curate/queues").json()
    names = {q["name"] for q in body["queues"]}
    assert names == {
        "contradictions",
        "drift",
        "superseded",
        "low-confidence",
        "missing-embeddings",
        "expiring",
        "never-accessed",
        "forgotten",
    }
    for q in body["queues"]:
        assert isinstance(q["count"], int)
        assert q["title"] and q["description"]


def test_audit_status_exposes_last_run_and_only_real_findings(client, chunks, db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_reflections
                (reflection_type, affected_chunks, action_taken, reasoning, confidence)
            VALUES ('audit', %s, 'flagged_drift_v2',
                    'Sampled 2 chunks, mean recall 0.50, threshold 0.30, 1 drifted.', 1.0)
            """,
            ([chunks[0]],),
        )
    db_connection.commit()

    status = client.get("/api/curate/audit").json()
    assert status["last_run"]["sampled"] == 2
    assert status["last_run"]["drifted"] == 1
    assert status["last_run"]["state"] == "findings"
    assert status["job"]["name"] == "audit-extraction"
    assert status["job"]["running"] is False
    items = client.get("/api/curate/queue/drift").json()["items"]
    assert [item["id"] for item in items] == [chunks[0]]


def test_audit_status_distinguishes_a_zero_sample_run(client, db_connection):
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO memory_reflections
                (reflection_type, affected_chunks, action_taken, reasoning, confidence)
            VALUES ('audit', ARRAY[]::bigint[], 'no_samples_v2',
                    'Sampled 0 chunks, mean recall 1.00, threshold 0.30, 0 drifted.', 1.0)
            """)
    db_connection.commit()

    status = client.get("/api/curate/audit").json()
    assert status["last_run"]["sampled"] == 0
    assert status["last_run"]["state"] == "no-samples"


def test_legacy_audit_does_not_turn_its_whole_sample_into_drift_findings(client, chunks, db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_reflections
                (reflection_type, affected_chunks, action_taken, reasoning, confidence)
            VALUES ('audit', %s, 'flagged_drift',
                    'Sampled 2 chunks, mean recall 0.50, threshold 0.30, 1 drifted.', 1.0)
            """,
            (chunks,),
        )
    db_connection.commit()

    status = client.get("/api/curate/audit").json()
    assert status["last_run"]["drifted"] == 1
    assert status["last_run"]["findings_available"] is False
    assert client.get("/api/curate/queue/drift").json()["items"] == []


def test_unknown_queue_404s(client):
    assert client.get("/api/curate/queue/nope").status_code == 404


def test_low_confidence_queue_contains_the_low_chunk(client, chunks):
    items = client.get("/api/curate/queue/low-confidence").json()["items"]
    assert chunks[0] in {i["id"] for i in items}
    assert chunks[1] not in {i["id"] for i in items}


def test_forget_hides_from_search_and_undo_restores(client, chunks):
    target = chunks[0]
    assert _findable(client, target)

    res = client.post("/api/curate/act", json={"action": "forget", "ids": [target]}).json()
    assert res["changed"] == 1
    assert res["undo_token"]

    assert not _findable(client, target), "a forgotten chunk must not appear in search"
    forgotten = client.get("/api/curate/queue/forgotten").json()["items"]
    assert target in {i["id"] for i in forgotten}

    undo = client.post("/api/curate/undo", json={"token": res["undo_token"]}).json()
    assert undo["changed"] == 1
    assert _findable(client, target), "undo did not restore the chunk"


def test_forget_preserves_the_row(client, chunks, db_connection):
    """Soft-delete, not delete — this is what makes undo possible at all."""
    target = chunks[0]
    client.post("/api/curate/act", json={"action": "forget", "ids": [target]})
    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM memory_chunks WHERE id = %s", (target,))
        row = cur.fetchone()
    assert row is not None, "forget deleted the row instead of marking it"
    assert row[0] == "forgotten"


def test_undo_token_cannot_be_replayed(client, chunks):
    res = client.post("/api/curate/act", json={"action": "forget", "ids": [chunks[0]]}).json()
    assert client.post("/api/curate/undo", json={"token": res["undo_token"]}).status_code == 200
    second = client.post("/api/curate/undo", json={"token": res["undo_token"]})
    assert second.status_code == 410
    assert "Forgotten queue" in second.json()["detail"], "expired undo must say what to do instead"


def test_unknown_undo_token_is_410(client):
    assert client.post("/api/curate/undo", json={"token": "nope"}).status_code == 410


def test_idempotency_key_applies_once(client, chunks):
    headers = {"Idempotency-Key": "same-key"}
    a = client.post("/api/curate/act", json={"action": "forget", "ids": chunks}, headers=headers).json()
    b = client.post("/api/curate/act", json={"action": "forget", "ids": chunks}, headers=headers).json()
    assert a == b, "a replayed submission must return the original result"
    assert a["undo_token"] == b["undo_token"], "a replay must not mint a second inverse"


def test_restore_from_forgotten_queue(client, chunks):
    target = chunks[0]
    client.post("/api/curate/act", json={"action": "forget", "ids": [target]})
    res = client.post("/api/curate/act", json={"action": "restore", "ids": [target]}).json()
    assert res["changed"] == 1
    assert _findable(client, target)


def test_raise_confidence_round_trip(client, chunks, db_connection):
    target = chunks[0]
    res = client.post(
        "/api/curate/act",
        json={"action": "raise_confidence", "ids": [target], "value": 0.85},
    ).json()
    assert res["changed"] == 1

    with db_connection.cursor() as cur:
        cur.execute("SELECT confidence FROM memory_chunks WHERE id = %s", (target,))
        assert float(cur.fetchone()[0]) == pytest.approx(0.85)

    client.post("/api/curate/undo", json={"token": res["undo_token"]})
    with db_connection.cursor() as cur:
        cur.execute("SELECT confidence FROM memory_chunks WHERE id = %s", (target,))
        assert float(cur.fetchone()[0]) == pytest.approx(0.30), "undo did not restore the prior value"


def test_raise_confidence_requires_a_value(client, chunks):
    r = client.post("/api/curate/act", json={"action": "raise_confidence", "ids": chunks})
    assert r.status_code == 422


def test_clear_expiry_round_trip(client, chunks, db_connection):
    target = chunks[1]
    res = client.post("/api/curate/act", json={"action": "clear_expiry", "ids": [target]}).json()
    assert res["changed"] == 1
    with db_connection.cursor() as cur:
        cur.execute("SELECT expires_at FROM memory_chunks WHERE id = %s", (target,))
        assert cur.fetchone()[0] is None

    client.post("/api/curate/undo", json={"token": res["undo_token"]})
    with db_connection.cursor() as cur:
        cur.execute("SELECT expires_at FROM memory_chunks WHERE id = %s", (target,))
        assert cur.fetchone()[0] is not None, "undo did not put the expiry back"


def test_empty_selection_is_a_noop(client):
    body = client.post("/api/curate/act", json={"action": "forget", "ids": []}).json()
    assert body["changed"] == 0 and body["undo_token"] is None


def test_undo_registry_expires_tokens():
    reg = UndoRegistry(ttl=0.0)
    token = reg.register({"op": "forget", "ids": [1]}, "x")
    assert reg.take(token) is None, "an expired token must not be usable"
