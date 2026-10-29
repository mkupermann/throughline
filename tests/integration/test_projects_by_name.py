"""Projects are identified by name, not by a registry row.

`projects` is enrichment (description, status, contacts) and lags reality
badly: 53 registered rows against 81 names actually in use on the author's
database. Keying retrieval and detail on `projects.id` therefore hid most of
a user's projects even though their memory was searchable.
"""

from __future__ import annotations

import pytest

from throughline.queries import find as F
from throughline.queries import skills as S

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env):
    """The API runs only against pytest's disposable PostgreSQL database."""
    from throughline.api import deps

    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as app:
        yield app
    deps.close_pool()


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO conversations (session_id, project_path, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/repo/registered', now(), 1, 'x'),
                   (gen_random_uuid(), '/repo/unregistered', now(), 1, 'y')
            """)
        cur.execute("""
            INSERT INTO memory_chunks (source_type, content, category, project_name)
            VALUES ('manual', 'a note about registered',   'insight', 'registered'),
                   ('manual', 'a note about unregistered', 'insight', 'unregistered')
            """)
        # Only one of the two projects has a registry row.
        cur.execute(
            "INSERT INTO projects (name, description, status) VALUES ('registered', 'has a registry row', 'active')"
        )
    db_connection.commit()
    return db_connection


def test_unregistered_project_is_listed(corpus):
    names = {r["title"] for r in F.browse(corpus, F.FindFilters(kinds=["project"]), limit=50).items}
    assert "unregistered" in names, "a project with memory but no registry row vanished"
    assert "registered" in names


def test_unregistered_project_is_searchable(corpus):
    res = F.find(corpus, "unregistered", filters=F.FindFilters(kinds=["project"]), limit=20)
    assert any(i["title"] == "unregistered" for i in res.items)


def test_registered_project_carries_its_id_unregistered_does_not(corpus):
    by_name = {r["title"]: r for r in F.browse(corpus, F.FindFilters(kinds=["project"]), limit=50).items}
    assert by_name["registered"]["id"] > 0
    assert by_name["unregistered"]["id"] == 0, "there is no registry id to report"


def test_detail_by_name_works_for_both(corpus):
    reg = S.get_project_by_name(corpus, "registered")
    unreg = S.get_project_by_name(corpus, "unregistered")

    assert reg is not None and reg["registered"] is True
    assert reg["description"] == "has a registry row"

    assert unreg is not None, "an unregistered project must still resolve"
    assert unreg["registered"] is False
    assert unreg["description"] is None


def test_detail_by_name_reports_activity(corpus):
    r = S.get_project_by_name(corpus, "unregistered")
    assert r["chunks_count"] == 1
    assert r["conversations_count"] == 1
    assert r["last_activity"] is not None


def test_unknown_project_name_is_none(corpus):
    assert S.get_project_by_name(corpus, "no-such-project") is None


def test_observed_names_cover_every_project_with_data(corpus):
    observed = {r["name"] for r in S.observed_project_names(corpus)}
    registered = {r["name"] for r in S.list_projects(corpus)}
    assert {"registered", "unregistered"} <= observed
    assert registered < observed, "the registry should be a subset of what exists"


@pytest.fixture()
def document_corpus(db_connection):
    """Synthetic history covering provenance, automation, and unplaced work."""
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO conversations (session_id, project_path, started_at, message_count, summary, generated_by)
            VALUES
              (gen_random_uuid(), '/repo/atlas', '2026-01-01T09:00:00Z', 1, 'First session', NULL),
              (gen_random_uuid(), '/repo/atlas', '2026-01-02T09:00:00Z', 2, 'Second session', NULL),
              (gen_random_uuid(), '/repo/atlas', '2026-01-02T10:00:00Z', 0, 'Empty session', NULL),
              (gen_random_uuid(), '/repo/atlas', '2026-01-03T09:00:00Z', 1, 'Generated session', 'throughline'),
              (gen_random_uuid(), '/repo/other', '2026-01-04T09:00:00Z', 1, 'Other session', NULL),
              (gen_random_uuid(), '/tmp', '2026-01-05T09:00:00Z', 1, 'Unplaced session', NULL)
            RETURNING id, project_name
            """)
        conversations = cur.fetchall()
        atlas = [row[0] for row in conversations if row[1] == "atlas"]
        other = next(row[0] for row in conversations if row[1] == "other")
        unplaced = next(row[0] for row in conversations if row[1] == "tmp")
        cur.execute(
            """
            INSERT INTO messages
              (conversation_id, role, content, content_blocks, tool_calls, tool_name, model, created_at)
            VALUES
              (%s, 'user', 'first message', NULL, NULL, NULL, NULL, '2026-01-01T09:00:00Z'),
              (%s, 'assistant', NULL,
               '[{"type":"tool_use","name":"Read","input":{"file_path":"src/app.ts"}}]'::jsonb,
               '[{"name":"Read"}]'::jsonb, 'Read', 'qwen2.5:7b-instruct',
               '2026-01-02T09:00:00Z'),
              (%s, 'assistant', 'generated message', NULL, NULL, NULL, NULL, '2026-01-03T09:00:00Z'),
              (%s, 'user', 'other project message', NULL, NULL, NULL, NULL, '2026-01-04T09:00:00Z'),
              (%s, 'user', 'unplaced message', NULL, NULL, NULL, NULL, '2026-01-05T09:00:00Z')
            """,
            (atlas[0], atlas[1], atlas[3], other, unplaced),
        )
        cur.execute(
            """
            INSERT INTO memory_chunks (source_type, source_id, content, category, confidence, project_name)
            VALUES
              ('conversation', %s, 'Use the stable ordering', 'decision', .95, 'atlas'),
              ('conversation', %s, 'Generated implementation detail', 'insight', .75, 'atlas'),
              ('manual', NULL, 'Keep the public API small', 'preference', .85, 'atlas'),
              ('conversation', %s, 'Unplaced knowledge', 'insight', .9, 'tmp'),
              ('conversation', 999999999, 'Keep orphaned knowledge readable', 'insight', .8, 'atlas')
            """,
            (atlas[0], atlas[3], unplaced),
        )
    db_connection.commit()
    return {
        "atlas": atlas,
        "other": other,
        "unplaced": unplaced,
    }


def test_project_context_aggregates_only_matching_history_with_provenance(client, document_corpus):
    """Removing project filters or source IDs would mix projects or break source navigation."""
    response = client.get("/api/projects/atlas/context?order=oldest&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["project"] == "atlas"
    assert body["sessionCount"] == 2
    assert body["messageCount"] == 2
    assert body["total"] == 2
    assert body["complete"] is False
    assert [message["content"] for message in body["messages"]] == ["first message"]
    assert body["messages"][0]["conversation_id"]
    assert isinstance(body["knowledge"][0]["confidence"], float)
    assert body["knowledge"] == [
        {
            "id": body["knowledge"][0]["id"],
            "type": "memory",
            "category": "decision",
            "content": "Use the stable ordering",
            "confidence": 0.95,
            "source_type": "conversation",
            "source_id": body["messages"][0]["conversation_id"],
        },
        {
            "id": body["knowledge"][1]["id"],
            "type": "memory",
            "category": "insight",
            "content": "Keep orphaned knowledge readable",
            "confidence": 0.8,
            "source_type": "conversation",
            "source_id": None,
        },
        {
            "id": body["knowledge"][2]["id"],
            "type": "memory",
            "category": "preference",
            "content": "Keep the public API small",
            "confidence": 0.85,
            "source_type": "manual",
            "source_id": None,
        },
    ]


def test_project_context_paginates_stably_and_can_include_generated(client, document_corpus):
    """Dropping the ID tie-breaker, reverse sort, or generated filter changes this visible history."""
    oldest = client.get("/api/projects/atlas/context?order=oldest&offset=1&limit=10").json()
    newest = client.get("/api/projects/atlas/context?order=newest&limit=10").json()
    generated = client.get("/api/projects/atlas/context?order=newest&includeGenerated=true&limit=10").json()

    assert [message["content"] for message in oldest["messages"]] == [None]
    assert [message["content"] for message in newest["messages"]] == [None, "first message"]
    assert [message["content"] for message in generated["messages"]] == ["generated message", None, "first message"]
    assert generated["sessionCount"] == 3
    assert "Generated implementation detail" in [item["content"] for item in generated["knowledge"]]


def test_project_context_keeps_complete_message_payloads(client, document_corpus):
    response = client.get("/api/projects/atlas/context?order=oldest&limit=10")

    assert response.status_code == 200
    tool_message = response.json()["messages"][1]
    assert tool_message["content"] is None
    assert tool_message["content_blocks"] == [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "src/app.ts"}}
    ]
    assert tool_message["tool_calls"] == [{"name": "Read"}]
    assert tool_message["tool_name"] == "Read"
    assert tool_message["model"] == "qwen2.5:7b-instruct"


def test_unplaced_context_uses_the_same_project_identity_as_sessions(client, document_corpus):
    sessions = client.get("/api/projects/%28no%20project%29/sessions").json()
    context = client.get("/api/projects/%28no%20project%29/context").json()

    assert sessions["total"] == 1
    assert [session["title"] for session in sessions["sessions"]] == ["Unplaced session"]
    assert context["sessionCount"] == 1
    assert context["messageCount"] == 1
    assert [message["content"] for message in context["messages"]] == ["unplaced message"]
    assert [item["content"] for item in context["knowledge"]] == ["Unplaced knowledge"]
