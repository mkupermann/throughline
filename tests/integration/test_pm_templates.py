"""Persistence, immutable versions, reference validation and transactional instances."""

import pytest
from fastapi.testclient import TestClient

from throughline.api import deps
from throughline.api.app import create_app
from throughline.api.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(db_env):
    deps.close_pool()
    with TestClient(create_app(Settings(web_dist=None)), raise_server_exceptions=False) as client:
        yield client
    deps.close_pool()


def test_seed_project_instantiates_real_linked_team_and_roles(client):
    templates = client.get("/api/pm/templates").json()["templates"]
    assert len(templates) >= 5
    project = next(t for t in templates if t["kind"] == "project")
    result = client.post(f"/api/pm/templates/{project['id']}/instantiate", json={"name": "My project", "version": 1})
    assert result.status_code == 200, result.text
    instance = result.json()
    assert instance["kind"] == "project"
    assert instance["template_snapshot"]["version"] == 1
    teams = client.get(f"/api/pm/projects/{instance['id']}/teams").json()["teams"]
    assert len(teams) == 1
    assert len(client.get("/api/pm/roles").json()["roles"]) == 3
    assert client.get(f"/api/pm/projects/{instance['id']}/tasks").json()["tasks"] == []


def test_versions_preserve_instances_and_detect_stale_edits(client):
    template = client.post(
        "/api/pm/templates", json={"kind": "role", "name": "Reviewer", "content": {"instructions": "Original"}}
    ).json()
    url = f"/api/pm/templates/{template['id']}"
    first = client.post(url + "/instantiate", json={"name": "First", "version": 1}).json()
    updated = client.patch(url, json={"content": {"instructions": "Revised"}, "expected_version": 1})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert client.patch(url, json={"name": "Stale", "expected_version": 1}).status_code == 409
    old = client.post(url + "/instantiate", json={"name": "Old version", "version": 1}).json()
    new = client.post(url + "/instantiate", json={"name": "New version", "version": 2}).json()
    assert old["instructions"] == "Original"
    assert new["instructions"] == "Revised"
    persisted = next(r for r in client.get("/api/pm/roles").json()["roles"] if r["id"] == first["id"])
    assert persisted["instructions"] == "Original"
    assert persisted["template_snapshot"]["version"] == 1


def test_invalid_references_and_archiving(client):
    bad = client.post(
        "/api/pm/templates",
        json={"kind": "project", "name": "Bad", "content": {"team_template": {"id": 999999, "version": 1}}},
    )
    assert bad.status_code == 422
    role = client.post("/api/pm/templates", json={"kind": "role", "name": "Role"}).json()
    url = f"/api/pm/templates/{role['id']}"
    assert (
        client.post(
            "/api/pm/templates",
            json={
                "kind": "project",
                "name": "Wrong kind",
                "content": {"team_template": {"id": role["id"], "version": 1}},
            },
        ).status_code
        == 422
    )
    assert client.patch(url, json={"archived": True}).status_code == 200
    assert client.post(url + "/instantiate", json={"name": "Blocked"}).status_code == 409
    assert client.post("/api/pm/templates/999999/instantiate", json={"name": "Missing"}).status_code == 404


@pytest.mark.parametrize("body", [{"name": " "}, {"name": "x", "version": 0}, {"name": "x", "surprise": True}])
def test_invalid_instance_inputs(client, body):
    assert client.post("/api/pm/templates/1/instantiate", json=body).status_code == 422


def test_link_failure_rolls_back_entire_project_tree(client, monkeypatch):
    from throughline.queries import pm_templates

    project = next(t for t in client.get("/api/pm/templates").json()["templates"] if t["kind"] == "project")
    original_execute = pm_templates.execute

    def fail_link(conn, sql, params=None):
        if "INSERT INTO pm_project_teams" in sql:
            raise ValueError("Simulated link failure")
        return original_execute(conn, sql, params)

    monkeypatch.setattr(pm_templates, "execute", fail_link)
    result = client.post(f"/api/pm/templates/{project['id']}/instantiate", json={"name": "Must roll back"})
    assert result.status_code == 422
    assert client.get("/api/pm/projects").json()["projects"] == []
    assert client.get("/api/pm/teams").json()["teams"] == []
    assert client.get("/api/pm/roles").json()["roles"] == []


def test_role_contract_is_available_in_editable_instructions(client):
    template = client.post(
        "/api/pm/templates",
        json={
            "kind": "role",
            "name": "Contract",
            "content": {"instructions": "Review", "expected_output": "Findings", "allowed_tools": "Browser"},
        },
    ).json()
    role = client.post(f"/api/pm/templates/{template['id']}/instantiate", json={"name": "Contract instance"}).json()
    assert "Expected output:\nFindings" in role["instructions"]
    assert "Requested tools (configuration required):\nBrowser" in role["instructions"]
    assert role["default_ai_tool"] is None


def test_project_and_team_briefs_are_editable_and_preserve_snapshot(client):
    templates = client.get("/api/pm/templates").json()["templates"]
    template = next(t for t in templates if t["kind"] == "project")
    result = client.post(
        f"/api/pm/templates/{template['id']}/instantiate", json={"name": "Visible brief", "description": "User brief"}
    ).json()
    assert result["description"].startswith("User brief")
    for key in ("objective", "stages", "deliverables", "acceptance_criteria"):
        assert template["content"][key] in result["description"]
    assert result["template_snapshot"]["content"] == template["content"]
    team = client.get(f"/api/pm/projects/{result['id']}/teams").json()["teams"][0]
    assert "Planned workflow:" in team["description"]
    assert "Review requirements (human verification required):" in team["description"]
    for key in ("workflow", "review_policy"):
        assert team["template_snapshot"]["content"][key] in team["description"]
    edited = client.patch(f"/api/pm/projects/{result['id']}", json={"description": "Edited brief"}).json()
    assert edited["description"] == "Edited brief"
    assert edited["template_snapshot"]["content"] == template["content"]


def test_domain_catalog_contract_and_pinned_references(client):
    templates = client.get("/api/pm/templates").json()["templates"]
    catalog = [t for t in templates if t["content"].get("catalog_key")]
    assert len(catalog) == 98
    assert len({t["content"]["catalog_key"] for t in catalog}) == 98
    categories = {
        "finance",
        "science",
        "education",
        "enterprise",
        "mid-size",
        "startup",
        "engineering",
        "product-design",
        "marketing",
        "operations",
        "security",
        "legal-compliance",
        "healthcare",
        "nonprofit",
    }
    assert {t["content"]["category"] for t in catalog} == categories
    by_id = {t["id"]: t for t in templates}
    for category in categories:
        group = [t for t in catalog if t["content"]["category"] == category]
        assert sorted(t["kind"] for t in group) == ["project"] * 3 + ["role"] * 3 + ["team"]
        for template in group:
            content = template["content"]
            if template["kind"] == "project":
                for field in ("objective", "stages", "deliverables", "acceptance_criteria"):
                    assert len(content[field]) > 35
                ref = content["team_template"]
                assert ref["version"] == 1
                assert by_id[ref["id"]]["kind"] == "team"
                assert by_id[ref["id"]]["content"]["category"] == category
            elif template["kind"] == "team":
                assert len(content["role_templates"]) == 3
                for ref in content["role_templates"]:
                    assert ref["version"] == 1
                    assert by_id[ref["id"]]["kind"] == "role"
                    assert by_id[ref["id"]]["content"]["category"] == category


@pytest.mark.parametrize("category", ["finance", "science", "education", "healthcare", "legal-compliance"])
def test_domain_project_preserves_contract_and_original_role_version(client, category):
    templates = client.get("/api/pm/templates").json()["templates"]
    project = next(t for t in templates if t["kind"] == "project" and t["content"].get("category") == category)
    team_ref = project["content"]["team_template"]
    team = next(t for t in templates if t["id"] == team_ref["id"])
    role_ref = team["content"]["role_templates"][2]
    role = next(t for t in templates if t["id"] == role_ref["id"])
    revised = {**role["content"], "instructions": "Changed future instructions"}
    update = client.patch(f"/api/pm/templates/{role['id']}", json={"expected_version": 1, "content": revised})
    assert update.status_code == 200
    assert update.json()["version"] == 2
    response = client.post(
        f"/api/pm/templates/{project['id']}/instantiate", json={"name": "Domain pilot", "version": 1}
    )
    assert response.status_code == 200, response.text
    instance = response.json()
    assert project["content"]["acceptance_criteria"] in instance["description"]
    assert instance["template_snapshot"]["content"]["category"] == category
    teams = client.get(f"/api/pm/projects/{instance['id']}/teams").json()["teams"]
    assert len(teams) == 1
    roles = client.get("/api/pm/roles").json()["roles"]
    assert len(roles) == 3
    review_role = next(r for r in roles if r["template_snapshot"]["id"] == role["id"])
    assert review_role["template_snapshot"]["version"] == 1
    assert role["content"]["instructions"] in review_role["instructions"]
    assert "Changed future instructions" not in review_role["instructions"]
    if category in {"finance", "healthcare", "legal-compliance"}:
        assert "Qualified domain professionals" in teams[0]["description"]


def test_schema_adoption_does_not_duplicate_or_replace_templates(db_connection):
    from throughline.jobs import migrate

    with db_connection.cursor() as cur:
        cur.execute("SELECT template_id, version, snapshot FROM pm_template_versions ORDER BY template_id, version")
        before = cur.fetchall()
    assert len(before) == 103
    assert migrate.cmd_migrate(db_connection, dry_run=False) == 0
    assert migrate.cmd_migrate(db_connection, dry_run=False) == 0
    with db_connection.cursor() as cur:
        cur.execute("SELECT template_id, version, snapshot FROM pm_template_versions ORDER BY template_id, version")
        assert cur.fetchall() == before
        cur.execute("SELECT count(*) FROM pm_templates")
        assert cur.fetchone()[0] == 103
