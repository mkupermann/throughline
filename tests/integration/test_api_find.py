"""Contract tests for /api/find, /api/find/facets and the detail routes."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from throughline.api.app import create_app  # noqa: E402
from throughline.api.settings import Settings  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env, monkeypatch):
    from throughline import embedding
    from throughline.api import deps

    # No embedding backend in CI, and we must not let a probe reach the
    # network from a test. Force the lexical-only path deterministically.
    monkeypatch.setattr(embedding, "backend_info",
                        lambda preferred="auto": embedding.BackendInfo(
                            available=False, reason="No embedding backend configured."))
    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as c:
        yield c
    deps.close_pool()


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, model, entrypoint, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/repo/alpha', 'claude', 'claude-code',
                    now() - interval '1 day', 2, 'A pgvector conversation')
            RETURNING id
            """
        )
        conv = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (%s, 'assistant', 'talking about pgvector indexes', now())",
            (conv,),
        )
        cur.execute(
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, tags, confidence, project_name, status)
            VALUES ('conversation', %s, 'we picked pgvector', 'decision', ARRAY['db'], 0.9, 'alpha', 'active')
            RETURNING id
            """,
            (conv,),
        )
        chunk = cur.fetchone()[0]
        cur.execute("INSERT INTO skills (name, description, path) VALUES ('pgvector-tool','x','/p')")
        cur.execute("INSERT INTO projects (name, description, status) VALUES ('alpha','pgvector','active')")
        cur.execute("INSERT INTO prompts (name, category, content) VALUES ('p','review','pgvector')")
    db_connection.commit()
    return {"conversation": conv, "memory": chunk}


def test_find_shape(client, corpus):
    r = client.get("/api/find", params={"q": "pgvector", "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "query", "items", "total", "limit", "offset", "modes", "notes", "backend",
    }
    assert body["query"] == "pgvector"
    assert body["total"] >= 1
    for item in body["items"]:
        assert set(item) == {
            "kind", "id", "title", "snippet", "project", "occurred_at",
            "category", "status", "confidence", "conversation_id", "score", "retrievers",
        }
        assert isinstance(item["id"], int)


def test_find_reports_lexical_only_and_explains_why(client, corpus):
    body = client.get("/api/find", params={"q": "pgvector"}).json()
    assert body["modes"] == ["lexical"]
    assert body["backend"]["available"] is False
    assert body["notes"], "a degraded search must explain itself"


def test_empty_query_returns_no_items(client, corpus):
    body = client.get("/api/find", params={"q": ""}).json()
    assert body["items"] == [] and body["total"] == 0


def test_kind_filter(client, corpus):
    body = client.get("/api/find", params={"q": "pgvector", "kind": "memory"}).json()
    assert body["items"]
    assert {i["kind"] for i in body["items"]} == {"memory"}


def test_multiple_kind_filters_are_unioned(client, corpus):
    body = client.get(
        "/api/find", params=[("q", "pgvector"), ("kind", "memory"), ("kind", "skill")]
    ).json()
    assert {i["kind"] for i in body["items"]} <= {"memory", "skill"}


def test_pagination_does_not_overlap(client, corpus):
    a = client.get("/api/find", params={"q": "pgvector", "limit": 2, "offset": 0}).json()
    b = client.get("/api/find", params={"q": "pgvector", "limit": 2, "offset": 2}).json()
    ka = {(i["kind"], i["id"]) for i in a["items"]}
    kb = {(i["kind"], i["id"]) for i in b["items"]}
    assert not (ka & kb), "pages overlap — ranking is not stable across requests"


def test_limit_is_bounded(client, corpus):
    assert client.get("/api/find", params={"q": "x", "limit": 10_000}).status_code == 422
    assert client.get("/api/find", params={"q": "x", "limit": 0}).status_code == 422


def test_confidence_filter_is_validated(client, corpus):
    assert client.get("/api/find", params={"q": "x", "min_confidence": 2}).status_code == 422


def test_occurred_at_serialises_as_string(client, corpus):
    for item in client.get("/api/find", params={"q": "pgvector"}).json()["items"]:
        assert item["occurred_at"] is None or isinstance(item["occurred_at"], str)


@pytest.fixture()
def provider_corpus(db_connection):
    """Three providers, so a filter has something to narrow away as well as
    something to keep — and a fourth (cursor) with no rows at all, to prove a
    provider-with-no-data case returns empty rather than everything."""
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, source_tool, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/p', 'claude_code', now(), 1, 'alpha'),
                   (gen_random_uuid(), '/p', 'hermes',      now(), 1, 'beta'),
                   (gen_random_uuid(), '/p', 'hermes',      now(), 1, 'gamma'),
                   (gen_random_uuid(), '/p', 'windsurf',    now(), 1, 'delta')
            RETURNING id, source_tool
            """
        )
        by_provider: dict[str, list[int]] = {}
        for conv_id, tool in cur.fetchall():
            by_provider.setdefault(tool, []).append(conv_id)
    db_connection.commit()
    return by_provider


def test_provider_filter_alone_browses(client, provider_corpus):
    """The primary interaction this whole plan exists for: click a provider
    chip, type nothing, see that provider's memory. `kind` is deliberately
    NOT passed here — `kind` alone was already enough to trip the router into
    browse mode before this fix, which would let this test pass on the buggy
    code for the wrong reason. Provider must trip it on its own."""
    body = client.get("/api/find", params={"provider": "hermes"}).json()
    got = {i["id"] for i in body["items"] if i["kind"] == "conversation"}
    assert got == set(provider_corpus["hermes"])
    assert provider_corpus["claude_code"][0] not in got


def test_two_providers_union_with_no_query(client, provider_corpus):
    body = client.get(
        "/api/find", params=[("provider", "hermes"), ("provider", "windsurf")]
    ).json()
    got = {i["id"] for i in body["items"] if i["kind"] == "conversation"}
    assert got == set(provider_corpus["hermes"]) | set(provider_corpus["windsurf"])


def test_provider_with_no_data_returns_empty_without_erroring(client, provider_corpus):
    r = client.get("/api/find", params={"provider": "cursor"})
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_facets(client, corpus):
    body = client.get("/api/find/facets").json()
    assert set(body) == {"kinds", "categories", "statuses", "projects", "tags"}
    assert all(isinstance(i["n"], int) and isinstance(i["value"], str)
               for group in body.values() for i in group)


@pytest.mark.parametrize("kind,key", [("conversation", "conversation"), ("memory", "memory")])
def test_detail_routes(client, corpus, kind, key):
    r = client.get(f"/api/detail/{kind}/{corpus[key]}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == kind
    assert body["record"]["id"] == corpus[key]


def test_conversation_detail_includes_related(client, corpus):
    body = client.get(f"/api/detail/conversation/{corpus['conversation']}").json()
    assert len(body["related"]["messages"]) == 1
    assert len(body["related"]["chunks"]) == 1


def test_detail_404_for_missing_record(client, corpus):
    assert client.get("/api/detail/memory/99999999").status_code == 404


def test_detail_rejects_unknown_kind(client, corpus):
    assert client.get("/api/detail/banana/1").status_code == 422
