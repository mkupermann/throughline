"""Atomic checkpoints, changed sources and cross-project isolation against real PostgreSQL."""

import psycopg2
import pytest
from fastapi.testclient import TestClient

from throughline.api import deps
from throughline.api.app import create_app
from throughline.api.settings import Settings
from throughline.jobs import incremental
from throughline.queries._exec import one
from throughline.queries.continuation import build

pytestmark = pytest.mark.integration


@pytest.fixture
def sources(db_connection):
    ids = []
    for project, generated in [("alpha", None), ("beta", None), ("alpha", "extractor")]:
        row = one(
            db_connection,
            """INSERT INTO conversations(session_id,project_path,started_at,message_count,generated_by)
            VALUES(gen_random_uuid(),%s,now(),5,%s) RETURNING id""",
            ("/repo/" + project, generated),
        )
        cid = row["id"]
        ids.append(cid)
        for i in range(5):
            one(
                db_connection,
                """INSERT INTO messages(conversation_id,role,content,created_at)
                VALUES(%s,'user',%s,now()) RETURNING id""",
                (cid, f"{project} evidence {i}"),
            )
    db_connection.commit()
    return ids


def test_empty_success_survives_restart_and_changed_source_is_pending(db_env, db_connection, sources, monkeypatch):
    from throughline.jobs import extract_memory

    calls = []
    monkeypatch.setattr(extract_memory, "extract_for_conversation", lambda cur, cid: calls.append(cid) or 0)
    item = incremental.pending(db_connection, "extract", project="alpha")[0]
    db_connection.commit()
    incremental.process_item(item, "extract", "synthetic/test")
    assert incremental.pending(db_connection, "extract", project="alpha") == []
    assert incremental.process_item(item, "extract", "synthetic/test")["skipped"]
    assert calls == [sources[0]]
    db_connection.rollback()
    one(
        db_connection,
        "UPDATE messages SET content=content||' changed' WHERE conversation_id=%s RETURNING id",
        (sources[0],),
    )
    db_connection.commit()
    assert len(incremental.pending(db_connection, "extract", project="alpha")) == 1


def test_failed_model_rolls_back_results_and_checkpoint(db_env, db_connection, sources, monkeypatch):
    from throughline.jobs import extract_memory

    def fail(cur, cid):
        cur.execute(
            "INSERT INTO memory_chunks(source_type,source_id,content,category) VALUES('conversation',%s,'partial','insight')",
            (cid,),
        )
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(extract_memory, "extract_for_conversation", fail)
    item = incremental.pending(db_connection, "extract", project="alpha")[0]
    db_connection.commit()
    with pytest.raises(RuntimeError):
        incremental.process_item(item, "extract", "synthetic/test")
    assert one(db_connection, "SELECT count(*) AS n FROM processing_checkpoints")["n"] == 0
    assert one(db_connection, "SELECT count(*) AS n FROM memory_chunks WHERE content='partial'")["n"] == 0


def test_edit_during_ai_call_rolls_back_stale_derivation(db_env, db_connection, sources, monkeypatch):
    from throughline.jobs import extract_memory

    def edit(cur, cid):
        other = psycopg2.connect(**incremental.get_db_config())
        try:
            with other:
                one(other, "UPDATE messages SET content='new source' WHERE conversation_id=%s RETURNING id", (cid,))
        finally:
            other.close()
        return 0

    monkeypatch.setattr(extract_memory, "extract_for_conversation", edit)
    item = incremental.pending(db_connection, "extract", project="alpha")[0]
    db_connection.commit()
    with pytest.raises(RuntimeError, match="Source changed"):
        incremental.process_item(item, "extract", "synthetic/test")
    assert one(db_connection, "SELECT count(*) AS n FROM processing_checkpoints")["n"] == 0


def test_continuation_budget_project_scope_and_generated_exclusion(db_connection, sources):
    result = build(db_connection, "alpha", 2000)
    assert len(result["markdown"]) <= 2000
    assert "alpha evidence" in result["markdown"]
    assert "beta evidence" not in result["markdown"]
    assert all(s["conversation_href"] == f"/c/{sources[0]}" for s in result["sources"])
    assert not result["empty"]
    assert build(db_connection, "missing")["empty"]
    with pytest.raises(ValueError):
        build(db_connection, "alpha", 100)


def test_data_view_explains_stored_vs_visible_and_endpoint_brief(db_env, sources):
    deps.close_pool()
    try:
        with TestClient(create_app(Settings(web_dist=None))) as client:
            data = client.get("/api/operate/data")
            assert data.status_code == 200, data.text
            counts = data.json()["counts"]
            assert counts["stored_conversations"] == 3
            assert counts["visible_conversations"] == 2
            assert counts["hidden_generated_conversations"] == 1
            assert data.json()["backup"]["verified"] is None
            brief = client.get("/api/projects/alpha/continue?max_chars=2000")
            assert brief.status_code == 200
            assert not brief.json()["empty"]
            assert client.post("/api/operate/process-recent", json={"workers": 99}).status_code == 422
    finally:
        deps.close_pool()


def test_concurrent_attempts_do_not_repeat_successful_source(db_env, db_connection, sources, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from throughline.jobs import extract_memory

    calls = []
    monkeypatch.setattr(extract_memory, "extract_for_conversation", lambda cur, cid: calls.append(cid) or 0)
    item = incremental.pending(db_connection, "extract", project="alpha")[0]
    db_connection.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: incremental.process_item(item, "extract", "synthetic/test"), range(2)))
    assert len(calls) == 1
    assert sum(r["skipped"] for r in results) == 1


def test_small_brief_still_includes_long_recent_evidence(db_connection, sources):
    one(
        db_connection,
        "UPDATE messages SET content=repeat('long evidence ',300) WHERE conversation_id=%s RETURNING id",
        (sources[0],),
    )
    db_connection.commit()
    brief = build(db_connection, "alpha", 2000)
    assert not brief["empty"]
    assert brief["truncated"]
    assert len(brief["markdown"]) <= 2000


def test_cli_workers_are_serial(db_env, db_connection, sources, monkeypatch):
    import threading

    seen = []
    monkeypatch.setattr(incremental, "model_label", lambda: ("claude/default", True))
    monkeypatch.setattr(
        incremental,
        "process_item",
        lambda *a: seen.append(threading.get_ident()) or {"skipped": False, "seconds": 0, "output_count": 0},
    )
    assert incremental.run_stage("extract", limit=10, workers=4) == 0
    assert len(set(seen)) == 1
