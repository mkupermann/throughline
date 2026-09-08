"""Security boundaries independent of a running database."""

import pytest
from fastapi.testclient import TestClient

from throughline.api.access import password_hash, password_matches, required_role, validate_origin
from throughline.api.app import create_app
from throughline.api.settings import Settings


def test_password_storage_is_salted_and_verifies_without_plaintext():
    password = "a sufficiently long fictional password"
    first, second = password_hash(password), password_hash(password)
    assert first != second and password not in first
    assert password_matches(password, first)
    assert not password_matches("a different long fictional password", first)
    assert not password_matches(password, "malformed")


@pytest.mark.parametrize(
    "origin",
    [
        "http://internal.example",
        "https://",
        "https://user:pass@host.example",
        "https://host.example/path",
        "https://host.example?key=secret",
        "https://host.example/#fragment",
    ],
)
def test_team_origin_rejects_insecure_or_ambiguous_origins(origin):
    with pytest.raises(ValueError):
        validate_origin(origin)


def test_team_mode_never_silently_falls_back_to_local():
    with pytest.raises(ValueError):
        Settings(auth_mode="typo")
    with pytest.raises(ValueError):
        Settings(auth_mode="team")
    assert Settings(auth_mode="team", public_url="https://workspace.example/").public_url == "https://workspace.example"


@pytest.mark.parametrize(
    "path,method,role",
    [
        ("/api/new-feature", "GET", "admin"),
        ("/api/new-feature", "POST", "admin"),
        ("/api/console/query", "POST", "admin"),
        ("/api/pm/tasks/1/events", "GET", "admin"),
        ("/api/story/project/session/1/artifact/2/0", "GET", "admin"),
        ("/api/story/project/history", "GET", "viewer"),
        ("/api/story/project/checkpoints", "POST", "editor"),
        ("/api/find/graph", "POST", "viewer"),
        ("/api/ask", "POST", "editor"),
    ],
)
def test_permissions_default_to_administrator(path, method, role):
    assert required_role(path, method) == role


def test_validation_error_does_not_echo_supplied_password():
    client = TestClient(create_app(Settings()))
    secret = "do-not-echo-this-secret" * 10
    response = client.post("/api/auth/login", json={"username": "person", "password": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_cli_server_preserves_team_environment(monkeypatch):
    import uvicorn

    from throughline.api.server import serve

    monkeypatch.setenv("THROUGHLINE_AUTH_MODE", "team")
    monkeypatch.setenv("THROUGHLINE_PUBLIC_URL", "http://localhost:8797")
    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(settings=app.state.settings, **kwargs))
    assert serve(host="127.0.0.1", port=8797) == 0
    assert captured["settings"].auth_mode == "team"
    assert captured["settings"].public_url == "http://localhost:8797"


def test_public_origin_uses_browser_canonical_host_and_default_ports():
    from throughline.api.access import matches_public_host

    assert validate_origin("https://WORKSPACE.example:443/") == "https://workspace.example"
    assert matches_public_host("workspace.example:443", "https://workspace.example")
    assert matches_public_host("LOCALHOST:80", "http://localhost")
    assert not matches_public_host("workspace.example:444", "https://workspace.example")
    assert not matches_public_host("evil.example@workspace.example", "https://workspace.example")
    assert not matches_public_host("workspace.example/path", "https://workspace.example")
