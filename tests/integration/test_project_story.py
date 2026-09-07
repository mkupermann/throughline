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


def test_explicit_output_file_and_prompt_keep_source_time(corpus):
    conn, ids, _ = corpus
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO messages (conversation_id, role, content, created_at, content_blocks)
            VALUES (%s, 'user', 'Make a report', '2026-08-01T10:01:23Z',
                    '[{"type":"file","path":"input.csv"}]'),
                   (%s, 'assistant', 'Report ready', '2026-08-01T10:02:34Z',
                    '[{"type":"output_file","path":"report.md"}]')""",
            (ids[0], ids[0]),
        )
    conn.commit()
    session = next(s for s in story.history(conn, "demo")["sessions"] if s["id"] == ids[0])
    assert session["file_count"] == 1
    assert session["prompt_at"].second == 23
    assert session["file_at"].second == 34
    detail = story.session_detail(conn, "demo", ids[0])
    assert any(m["content_blocks"] == [{"type": "output_file", "path": "report.md"}] for m in detail["messages"])


def test_meaningful_previews_keep_source_ids_and_recovery_independent_of_search(corpus):
    conn, ids, _ = corpus
    with conn.cursor() as cur:
        cur.execute("DELETE FROM messages WHERE conversation_id=%s", (ids[0],))
        for role, text, timestamp in [
            (
                "user",
                "\n<recommended_plugins>noise</recommended_plugins>\n<environment_context>host</environment_context>\n",
                "2026-08-01T10:00:00Z",
            ),
            (
                "user",
                '<in-app-browser-context source="ambient-ui-state">noise</in-app-browser-context>\n## My request:\nBuild a nebula',
                "2026-08-01T10:01:00Z",
            ),
            ("assistant", "The first version is ready.", "2026-08-01T10:02:00Z"),
            ("user", "Add a journey", "2026-08-01T10:03:00Z"),
            ("assistant", "[Tool: exec] command", "2026-08-01T10:04:00Z"),
        ]:
            cur.execute(
                "INSERT INTO messages(conversation_id,uuid,role,content,created_at) VALUES(%s,%s,%s,%s,%s)",
                (ids[0], str(uuid4()), role, text, timestamp),
            )
        cur.execute("UPDATE conversations SET summary='<recommended_plugins>noise' WHERE id=%s", (ids[0],))
    conn.commit()
    h = story.history(conn, "demo", path="/team/a/demo")
    s = h["sessions"][0]
    assert s["opening"] == "Build a nebula"
    assert s["title"] == "Build a nebula"
    assert s["answer"] == "The first version is ready."
    assert s["latest_request"] == "Add a journey"
    assert s["awaiting_answer"]
    assert s["prompt_id"] and s["answer_id"] and s["latest_request_id"]
    assert (
        story.history(conn, "demo", path="/team/a/demo", q="NO_MATCH")["recovery"]["latest_request"] == "Add a journey"
    )
    original = story.session_detail(conn, "demo", ids[0], path="/team/a/demo")["messages"][0]
    assert "<recommended_plugins>" in original["content"]


def test_artifact_reference_availability_and_scope(corpus, tmp_path):
    from throughline.queries.presentation import artifacts

    conn, ids, _ = corpus
    local = tmp_path / "animation.html"
    local.write_text("example")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("Outside the project; must not be downloadable")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO messages(conversation_id,uuid,role,content,created_at) VALUES(%s,%s,'assistant',%s,now())",
            (
                ids[0],
                str(uuid4()),
                "[Animation](animation.html) [Missing](missing.html) [External](https://example.org/a) [Escape](../outside.txt)",
            ),
        )
    conn.commit()
    result = artifacts(conn, ids[0], str(tmp_path))
    assert [(x["label"], x["availability"]) for x in result] == [
        ("Animation", "available"),
        ("Missing", "unavailable"),
        ("Escape", "unavailable"),
    ]
    assert all(x["message_id"] for x in result)


def test_artifact_download_requires_source_and_project_scope(corpus, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from throughline.api.routers import story as router

    conn, ids, _ = corpus
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "result.html").write_text("<script>example</script>")
    with conn.cursor() as cur:
        cur.execute("UPDATE conversations SET project_path=%s WHERE id=%s", (str(folder), ids[0]))
        cur.execute(
            "INSERT INTO messages(conversation_id,uuid,role,content,created_at) VALUES(%s,%s,'assistant','[Result](result.html)',now()) RETURNING id",
            (ids[0], str(uuid4())),
        )
        mid = cur.fetchone()[0]
    conn.commit()

    @contextmanager
    def connect(_):
        yield conn

    monkeypatch.setattr(router, "connection", connect)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[router.get_settings] = lambda: None
    client = TestClient(app)
    url = f"/story/demo/session/{ids[0]}/artifact/{mid}/0"
    response = client.get(url)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(url.replace("/demo/", "/other/")).status_code == 404
    assert client.get(url.replace(f"/{mid}/", f"/{mid + 10000}/")).status_code == 404


def test_file_references_preserve_parentheses_and_spaces():
    from throughline.queries.presentation import markdown_files

    assert list(
        markdown_files("[A](/project/Atlas (demo)/result.html) [B](<a b.md>) [D]( spaced.md ) [C](a\\(b\\).md)")
    ) == [("A", "/project/Atlas (demo)/result.html"), ("B", "a b.md"), ("D", "spaced.md"), ("C", "a(b).md")]
