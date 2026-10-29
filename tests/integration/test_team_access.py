"""Exercise real sessions and roles against PostgreSQL, including revocation and audit."""

import pytest
from fastapi.testclient import TestClient

from throughline.api.access import password_hash
from throughline.api.app import create_app
from throughline.api.deps import close_pool
from throughline.api.settings import Settings
from throughline.queries._exec import one, rows

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost"
PASSWORD = "a fictional password for testing"
HEADERS = {"origin": ORIGIN, "x-throughline-request": "1"}


@pytest.fixture
def workspace(db_env, db_connection):
    encoded = password_hash(PASSWORD)
    for role in ("viewer", "editor", "admin"):
        one(
            db_connection,
            "INSERT INTO access_users(username,display_name,password_hash,role) VALUES (%s,%s,%s,%s)",
            (role, role, encoded, role),
        )
    one(
        db_connection,
        "INSERT INTO pm_ai_providers(name,provider_type,api_key) VALUES ('preserved','openai','existing-private-key')",
    )
    db_connection.commit()
    close_pool()
    with TestClient(create_app(Settings(auth_mode="team", public_url=ORIGIN)), base_url=ORIGIN) as client:
        yield client, db_connection
    close_pool()


def sign_in(client, role):
    result = client.post("/api/auth/login", json={"username": role, "password": PASSWORD}, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert "httponly" in result.headers["set-cookie"].lower()
    return {**HEADERS, "x-csrf-token": result.json()["csrf_token"]}


def test_anonymous_and_viewer_cannot_reach_admin_surfaces(workspace):
    client, conn = workspace
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 403
    headers = sign_in(client, "viewer")
    assert client.get("/api/projects/all").status_code == 200
    for path in (
        "/api/ai/settings",
        "/api/pm/ai-providers",
        "/api/access/users",
        "/api/access/audit",
        "/api/operate/all",
        "/api/console/schema",
        "/api/export/browse",
    ):
        assert client.get(path).status_code == 403, path
    assert (
        client.post(
            "/api/console/query", json={"sql": "SELECT api_key FROM pm_ai_providers"}, headers=headers
        ).status_code
        == 403
    )
    assert client.post("/api/curate/chunk", json={}, headers=headers).status_code == 403
    assert one(conn, "SELECT api_key FROM pm_ai_providers WHERE name='preserved'")["api_key"] == "existing-private-key"


def test_csrf_logout_and_role_revocation(workspace):
    client, conn = workspace
    headers = sign_in(client, "admin")
    cookie = client.cookies.get("throughline_session")
    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 403
    assert client.post("/api/auth/logout", headers={**headers, "origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    client.cookies.set("throughline_session", cookie)
    assert client.get("/api/projects/all").status_code == 401
    sign_in(client, "editor")
    one(conn, "UPDATE access_users SET enabled=false WHERE username='editor'")
    conn.commit()
    assert client.get("/api/projects/all").status_code == 401


def test_audit_omits_secrets_and_last_admin_is_protected(workspace):
    client, conn = workspace
    headers = sign_in(client, "admin")
    admin = one(conn, "SELECT id FROM access_users WHERE username='admin'")
    assert (
        client.put(
            f"/api/access/users/{admin['id']}", json={"role": "viewer", "enabled": True}, headers=headers
        ).status_code
        == 409
    )
    result = client.post(
        "/api/access/users",
        json={"username": "new.editor", "display_name": "Editor", "password": PASSWORD, "role": "editor"},
        headers=headers,
    )
    assert result.status_code == 201, result.text
    log = client.get("/api/access/audit")
    assert log.status_code == 200 and "existing-private-key" not in log.text and PASSWORD not in log.text
    events = log.json()["events"]
    assert any(e["entity_type"] == "access_users" and e["actor"] == f"user:{admin['id']}" for e in events)
    assert all("password_hash" not in r for r in client.get("/api/access/users").json()["users"])
    assert not any(cookie in str(rows(conn, "SELECT * FROM access_sessions")) for cookie in client.cookies.values())


def test_expired_session_wrong_host_and_rate_limit(workspace):
    client, conn = workspace
    sign_in(client, "viewer")
    one(conn, "UPDATE access_sessions SET last_seen_at=now()-interval '31 minutes'")
    conn.commit()
    assert client.get("/api/projects/all").status_code == 401
    assert client.get("/api/auth/session", headers={"host": "evil.example"}).status_code == 400
    from throughline.api.access import token_hash

    one(
        conn,
        "INSERT INTO access_attempts(key,attempts) VALUES (%s,30) ON CONFLICT(key) DO UPDATE SET attempts=30",
        (token_hash("account:viewer"),),
    )
    conn.commit()
    assert (
        client.post("/api/auth/login", json={"username": "viewer", "password": PASSWORD}, headers=HEADERS).status_code
        == 429
    )


def test_own_password_change_keeps_actor_after_rate_limit_commit(workspace):
    client, conn = workspace
    headers = sign_in(client, "editor")
    user = one(conn, "SELECT id FROM access_users WHERE username='editor'")
    result = client.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "another fictional testing password"},
        headers=headers,
    )
    assert result.status_code == 200
    event = one(
        conn,
        "SELECT actor FROM access_audit WHERE entity_type='access_users' AND entity_id=%s ORDER BY id DESC LIMIT 1",
        (str(user["id"]),),
    )
    assert event["actor"] == f"user:{user['id']}"
    assert client.get("/api/projects/all").status_code == 401
