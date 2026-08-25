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


@pytest.mark.integration
def test_resolve_assignment_merges_role_and_member(db_connection):
    project = Q.create_pm_project(db_connection, name="P", token_budget=500_000)
    team = Q.create_team(db_connection, name="T", token_budget=300_000)
    role = Q.create_role(
        db_connection, name="Executor", default_ai_tool="aider",
        default_ai_model="qwen3-coder:30b",
        instructions="Follow the spec exactly.", token_budget=200_000,
    )
    member = Q.create_member(
        db_connection, name="Michael", member_type="human",
        instructions="Prefer concise diffs.", token_budget=100_000,
    )
    a = Q.create_assignment(
        db_connection, pm_project_id=project["id"], team_id=team["id"],
        role_id=role["id"], member_id=member["id"],
    )

    resolved = Q.resolve_assignment(db_connection, a["id"])
    assert resolved["ai_tool"] == "aider"
    assert resolved["ai_model"] == "qwen3-coder:30b"
    assert resolved["instructions"] == "Follow the spec exactly.\n\nPrefer concise diffs."
    assert resolved["role_budget"] == 200_000
    assert resolved["member_budget"] == 100_000
    assert resolved["team_budget"] == 300_000
    assert resolved["project_budget"] == 500_000


@pytest.mark.integration
def test_resolve_assignment_ai_override_wins(db_connection):
    project = Q.create_pm_project(db_connection, name="P2")
    team = Q.create_team(db_connection, name="T2")
    role = Q.create_role(db_connection, name="Executor", default_ai_tool="aider", default_ai_model="qwen3-coder:30b")
    member = Q.create_member(db_connection, name="Devstral Agent", member_type="agent")
    a = Q.create_assignment(
        db_connection, pm_project_id=project["id"], team_id=team["id"],
        role_id=role["id"], member_id=member["id"], ai_model="devstral",
    )
    resolved = Q.resolve_assignment(db_connection, a["id"])
    assert resolved["ai_tool"] == "aider"       # inherited, no override given
    assert resolved["ai_model"] == "devstral"   # override wins
