"""Regression tests against real PostgreSQL: provenance must survive refresh."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from throughline.adapters.base import NormalisedConversation, NormalisedMessage
from throughline.adapters.writer import _replace_messages
from throughline.api.routers.story import Checkpoint
from throughline.queries import story

pytestmark = pytest.mark.integration


@pytest.fixture
def corpus(db_connection):
    conn = db_connection
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO conversations (session_id, project_path, started_at, summary, source_tool)
            VALUES (gen_random_uuid(), '/team/a/demo', '2026-08-01T10:00:00Z', 'Compare methods', 'codex'),
                   (gen_random_uuid(), '/team/b/demo', '2026-08-02T10:00:00Z', 'Unrelated work', 'claude_code'),
                   (gen_random_uuid(), '/team/a/other', '2026-08-03T10:00:00Z', 'Other project', 'codex')
            RETURNING id""")
        ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            """INSERT INTO messages (conversation_id, uuid, role, content, created_at)
            VALUES (%s, %s, 'assistant', 'Method B failed the replication.', now()) RETURNING id""",
            (ids[0], str(uuid4())),
        )
        message = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO memory_chunks (source_type, source_id, category, content)
            VALUES ('conversation', %s, 'insight', 'Method B is reliable')""",
            (ids[0],),
        )
    conn.commit()
    return conn, ids, message


def test_folder_isolation_and_search(corpus):
    conn, ids, _ = corpus
    all_folders = story.history(conn, "demo")
    assert all_folders["total"] == 2
    assert len(all_folders["paths"]) == 2
    result = story.history(conn, "demo", path="/team/a/demo", q="replication")
    assert [s["id"] for s in result["sessions"]] == ids[:1]
    assert story.session_detail(conn, "demo", ids[1], path="/team/a/demo") is None
    assert story.history(conn, "demo", q="' OR 1=1 --")["total"] == 0


def test_checkpoint_rejects_foreign_session_and_message(corpus):
    conn, ids, message = corpus
    note = dict(kind="status", content="Reviewed result", conversation_id=ids[2], message_id=message)
    assert story.checkpoint(conn, "demo", Checkpoint(**note)) is None
    note["conversation_id"] = ids[1]
    assert story.checkpoint(conn, "demo", Checkpoint(**note)) is None
    note["conversation_id"] = ids[0]
    assert story.checkpoint(conn, "demo", Checkpoint(path="/team/b/demo", **note)) is None


def test_checkpoint_history_and_source_changes(corpus):
    conn, ids, message = corpus
    for text in ("Initially preferred B", "B failed; investigate A"):
        assert story.checkpoint(
            conn, "demo", Checkpoint(kind="status", content=text, conversation_id=ids[0], message_id=message)
        )
    history = story.history(conn, "demo")["checkpoints"]
    assert [x["content"] for x in history] == ["B failed; investigate A", "Initially preferred B"]
    assert not history[0]["source_changed"]
    with conn.cursor() as cur:
        cur.execute("UPDATE messages SET content=%s WHERE id=%s", ("Corrected result", message))
    conn.commit()
    assert story.history(conn, "demo")["checkpoints"][0]["source_changed"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM conversations WHERE id=%s", (ids[0],))
    conn.commit()
    checkpoint = story.history(conn, "demo")["checkpoints"][0]
    assert not checkpoint["source_available"]
    assert checkpoint["source_excerpt"] == "Method B failed the replication."


def test_refresh_preserves_message_id_and_checkpoint(corpus):
    conn, ids, message = corpus
    with conn.cursor() as cur:
        cur.execute("SELECT uuid FROM messages WHERE id=%s", (message,))
        source_uuid = str(cur.fetchone()[0])
    story.checkpoint(
        conn, "demo", Checkpoint(kind="status", content="Reviewed", conversation_id=ids[0], message_id=message)
    )
    conv = NormalisedConversation(
        session_id=str(uuid4()),
        project_path="/team/a/demo",
        model=None,
        entrypoint=None,
        started_at=datetime.now(timezone.utc),
        ended_at=None,
        messages=[
            NormalisedMessage(role="assistant", content="Method B failed the replication.", uuid=source_uuid),
            NormalisedMessage(role="user", content="Try A next", uuid=str(uuid4())),
        ],
    )
    for _ in range(2):
        with conn.cursor() as cur:
            assert _replace_messages(cur, ids[0], conv) == 2
        conn.commit()
    detail = story.session_detail(conn, "demo", ids[0])
    assert detail["total"] == 2
    assert next(m["id"] for m in detail["messages"] if str(m["uuid"]) == source_uuid) == message
    assert story.history(conn, "demo")["checkpoints"][0]["source_message_id"] == message
    # Removed source stays attributable through the stored excerpt, not a dangling link.
    conv.messages = conv.messages[1:]
    with conn.cursor() as cur:
        _replace_messages(cur, ids[0], conv)
    conn.commit()
    checkpoint = story.history(conn, "demo")["checkpoints"][0]
    assert not checkpoint["message_available"]
    assert checkpoint["source_excerpt"] == "Method B failed the replication."


def test_machine_extraction_is_not_confirmed_state(corpus):
    conn, ids, _ = corpus
    assert story.history(conn, "demo")["checkpoints"] == []
    assert story.session_detail(conn, "demo", ids[0])["knowledge"][0]["status"] == "active"


def test_old_goal_survives_many_newer_status_entries(corpus):
    conn, ids, message = corpus
    story.checkpoint(
        conn, "demo", Checkpoint(kind="goal", content="Original goal", conversation_id=ids[0], message_id=message)
    )
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO project_checkpoints
            (project_name, kind, content, source_conversation_id, source_session_id, source_excerpt)
            SELECT 'demo', 'status', 'A newer status', id, session_id, ''
            FROM conversations CROSS JOIN generate_series(1, 110) WHERE id=%s""",
            (ids[0],),
        )
    conn.commit()
    result = story.history(conn, "demo")
    assert len(result["checkpoints"]) == 100
    assert next(c for c in result["latest_checkpoints"] if c["kind"] == "goal")["content"] == "Original goal"


def test_story_api_source_validation_and_pagination(corpus, db_env):
    from fastapi.testclient import TestClient

    from throughline.api import deps
    from throughline.api.app import create_app
    from throughline.api.settings import Settings

    _, ids, message = corpus
    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None))) as client:
        first = client.get("/api/story/demo/history?limit=1").json()
        assert first["has_more"]
        second = client.get("/api/story/demo/history?limit=1&offset=1").json()
        assert first["sessions"][0]["id"] != second["sessions"][0]["id"]
        assert client.get(f"/api/story/other/session/{ids[0]}").status_code == 404
        body = dict(kind="next", content="Try method C", conversation_id=ids[0], message_id=message)
        assert client.post("/api/story/demo/checkpoints", json=body).status_code == 201
        assert client.post("/api/story/other/checkpoints", json=body).status_code == 422
        body["content"] = "   "
        assert client.post("/api/story/demo/checkpoints", json=body).status_code == 422
    deps.close_pool()


def test_empty_search_does_not_highlight_every_message(corpus):
    conn, ids, _ = corpus
    detail = story.session_detail(conn, "demo", ids[0])
    assert detail["matches"] == []
    assert not any(m["matches"] for m in detail["messages"])
    assert len(story.session_detail(conn, "demo", ids[0], q="replication")["matches"]) == 1
    assert story.history(conn, "demo", providers=["vibe"])["total"] == 0
    assert story.session_detail(conn, "demo", ids[0], providers=["vibe"]) is None


def test_only_explicit_source_relationships_are_resolved(corpus):
    conn, ids, _ = corpus
    assert story.session_detail(conn, "demo", ids[0])["relations"] == []
    with conn.cursor() as cur:
        cur.execute("UPDATE conversations SET metadata=%s WHERE id=%s", ('{"codex_session_id":"parent-raw"}', ids[2]))
        cur.execute(
            "UPDATE conversations SET metadata=%s WHERE id=%s",
            ('{"source_metadata":{"forked_from_id":"parent-raw"}}', ids[0]),
        )
    conn.commit()
    edge = story.session_detail(conn, "demo", ids[0])["relations"][0]
    assert edge["target_id"] == ids[2]
    assert edge["source_field"] == "source_metadata.forked_from_id"
    with conn.cursor() as cur:
        cur.execute("DELETE FROM conversations WHERE id=%s", (ids[2],))
    conn.commit()
    assert story.session_detail(conn, "demo", ids[0])["relations"][0]["resolution"] == "not imported"


def test_library_includes_old_projects(corpus, db_env):
    from fastapi.testclient import TestClient

    from throughline.api import deps
    from throughline.api.app import create_app
    from throughline.api.settings import Settings

    conn, ids, _ = corpus
    with conn.cursor() as cur:
        cur.execute("UPDATE conversations SET started_at='2020-01-01T00:00:00Z' WHERE id=%s", (ids[2],))
    conn.commit()
    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None))) as client:
        projects = client.get("/api/projects/all").json()["projects"]
        assert "other" in {p["project"] for p in projects}
        assert client.get("/api/projects/all?provider=vibe").json()["projects"] == []
    deps.close_pool()
