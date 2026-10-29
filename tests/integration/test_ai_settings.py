import pytest
from fastapi.testclient import TestClient

from throughline.api.app import create_app
from throughline.api.settings import Settings
from throughline.queries import project_names

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env, db_connection, monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def connection(settings):
        yield db_connection

    monkeypatch.setattr("throughline.api.routers.ai_settings.connection", connection)
    monkeypatch.setattr("throughline.ai_runtime.bridge", lambda *a, **kw: {"clis": {"vibe": {"installed": True}}})
    return TestClient(create_app(Settings(web_dist=None)))


def test_settings_roundtrip_and_secret_never_returned(client, db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO pm_ai_providers(name,provider_type,base_url,api_key) VALUES ('local','ollama','http://localhost:11434','never-return') RETURNING id"
        )
        pid = cur.fetchone()[0]
    result = client.put("/api/ai/settings/titles", json={"provider_id": pid, "model": "local-model"})
    assert result.status_code == 200
    response = client.get("/api/ai/settings")
    assert response.status_code == 200 and "never-return" not in response.text
    assert response.json()["bindings"][0]["model"] == "local-model"
    assert client.put("/api/ai/settings/embeddings", json={"cli": "vibe", "model": ""}).status_code == 422
    assert client.put("/api/ai/settings/titles", json={"provider_id": pid, "cli": "codex"}).status_code == 422


def test_user_name_replaces_suggestion_and_clears_model_origin(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations(session_id,project_path,started_at) VALUES (gen_random_uuid(),'/work/demo',now()) RETURNING id"
        )
        cid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO project_names(project_key,display_name,name_origin,source_conversation_ids) VALUES ('demo','Suggested subject','model',%s)",
            ([cid],),
        )
    identity = project_names.attach(db_connection, [{"project": "demo"}])[0]
    assert identity["name_origin"] == "model" and identity["source_conversation_ids"] == [cid]
    project_names.save(db_connection, "demo", "My confirmed label")
    identity = project_names.attach(db_connection, [{"project": "demo"}])[0]
    assert identity["name_origin"] == "user" and identity["source_conversation_ids"] == []
