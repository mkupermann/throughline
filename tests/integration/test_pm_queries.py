import pytest

from throughline.queries import pm as Q


@pytest.mark.integration
def test_create_and_list_role(db_connection):
    role = Q.create_role(
        db_connection, name="Executor", default_ai_tool="aider",
        default_ai_model="ollama_chat/qwen3-coder:30b", token_budget=200_000,
    )
    assert role["name"] == "Executor"
    assert role["default_ai_tool"] == "aider"
    assert role["skill_refs"] == []

    roles = Q.list_roles(db_connection)
    assert any(r["id"] == role["id"] for r in roles)


@pytest.mark.integration
def test_create_member_rejects_bad_type(db_connection):
    with pytest.raises(Exception):
        Q.create_member(db_connection, name="X", member_type="robot")
    db_connection.rollback()


@pytest.mark.integration
def test_create_and_list_team(db_connection):
    team = Q.create_team(db_connection, name="Demo Team", token_budget=1_000_000)
    assert team["name"] == "Demo Team"
    assert any(t["id"] == team["id"] for t in Q.list_teams(db_connection))


@pytest.mark.integration
def test_project_team_role_linking(db_connection):
    project = Q.create_pm_project(db_connection, name="Razor1911 Tribute")
    team = Q.create_team(db_connection, name="Demoscene Team")
    role = Q.create_role(db_connection, name="Executor")

    Q.link_project_team(db_connection, project["id"], team["id"])
    Q.link_team_role(db_connection, team["id"], role["id"])

    teams = Q.get_project_teams(db_connection, project["id"])
    assert len(teams) == 1
    assert teams[0]["id"] == team["id"]
    assert any(r["id"] == role["id"] for r in teams[0]["roles"])
