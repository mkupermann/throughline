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
