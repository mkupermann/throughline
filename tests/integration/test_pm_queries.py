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
        document_refs=["spec.md", "shared.md"],
    )
    member = Q.create_member(
        db_connection, name="Michael", member_type="human",
        instructions="Prefer concise diffs.", token_budget=100_000,
        document_refs=["shared.md", "shared.md", "notes.md"],
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
    # role docs first, then member's novel docs, each appearing once
    assert resolved["document_refs"] == ["spec.md", "shared.md", "notes.md"]


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


@pytest.mark.integration
def test_task_lifecycle_and_token_rollup(db_connection):
    project = Q.create_pm_project(db_connection, name="P3")
    team = Q.create_team(db_connection, name="T3")
    task = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"],
        title="Add subtract()", run_id="run-abc", repo_path="/tmp/x",
        log_dir="/tmp/x/.ai-pipeline/run-abc", pid=1234,
    )
    assert task["status"] == "pending"

    Q.set_task_status(db_connection, task["id"], "running")
    assert Q.get_task(db_connection, task["id"])["status"] == "running"
    assert any(t["id"] == task["id"] for t in Q.list_running_tasks(db_connection))

    Q.add_task_event(db_connection, task_id=task["id"], step="analyst", event_type="started")
    Q.add_task_event(
        db_connection, task_id=task["id"], step="executor", event_type="log_update",
        iteration=1, tokens_used=340,
    )
    Q.add_task_event(
        db_connection, task_id=task["id"], step="tester", event_type="verdict",
        iteration=1, message="VERDICT: PASS", tokens_used=210,
    )

    total = Q.recompute_task_tokens(db_connection, task["id"])
    assert total == 550
    assert Q.get_task(db_connection, task["id"])["tokens_used"] == 550

    Q.set_task_status(db_connection, task["id"], "pass")
    assert Q.get_task(db_connection, task["id"])["status"] == "pass"
    assert not any(t["id"] == task["id"] for t in Q.list_running_tasks(db_connection))


@pytest.mark.integration
def test_list_tasks_for_project_running_first(db_connection):
    project = Q.create_pm_project(db_connection, name="P4")
    team = Q.create_team(db_connection, name="T4")
    old_done = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="old",
        run_id="r-old", repo_path="/tmp/x", log_dir="/tmp/x/.ai-pipeline/r-old",
    )
    Q.set_task_status(db_connection, old_done["id"], "pass")
    running = Q.create_task(
        db_connection, pm_project_id=project["id"], team_id=team["id"], title="new",
        run_id="r-new", repo_path="/tmp/x", log_dir="/tmp/x/.ai-pipeline/r-new",
    )
    Q.set_task_status(db_connection, running["id"], "running")

    tasks = Q.list_tasks_for_project(db_connection, project["id"])
    assert tasks[0]["id"] == running["id"]  # running sorts first regardless of age


@pytest.mark.integration
def test_get_skill_names_resolves_ids_to_names_sorted(db_connection):
    # skills.name and skills.path are NOT NULL per sql/schema.sql — insert
    # directly rather than going through a skills-import job that does not
    # exist in this domain.
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO skills (name, path) VALUES (%s, %s) RETURNING id",
            ("zebra-skill", "/skills/zebra-skill"),
        )
        zebra_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO skills (name, path) VALUES (%s, %s) RETURNING id",
            ("apple-skill", "/skills/apple-skill"),
        )
        apple_id = cur.fetchone()[0]
    db_connection.commit()

    names = Q.get_skill_names(db_connection, [zebra_id, apple_id])
    assert names == ["apple-skill", "zebra-skill"]  # ORDER BY name


@pytest.mark.integration
def test_get_skill_names_empty_ids_returns_empty_list(db_connection):
    assert Q.get_skill_names(db_connection, []) == []


@pytest.mark.integration
def test_register_existing_run_rejects_path_traversal_run_id(db_connection, tmp_path):
    project = Q.create_pm_project(db_connection, name="RegTravP")
    team = Q.create_team(db_connection, name="RegTravT")

    with pytest.raises(ValueError):
        Q.register_existing_run(
            db_connection, pm_project_id=project["id"], team_id=team["id"],
            title="t", repo_path=str(tmp_path), run_id="../../etc/passwd",
        )


@pytest.mark.integration
def test_register_existing_run_rejects_missing_log_dir(db_connection, tmp_path):
    project = Q.create_pm_project(db_connection, name="RegMissP")
    team = Q.create_team(db_connection, name="RegMissT")

    with pytest.raises(FileNotFoundError):
        Q.register_existing_run(
            db_connection, pm_project_id=project["id"], team_id=team["id"],
            title="t", repo_path=str(tmp_path), run_id="never-existed",
        )


@pytest.mark.integration
def test_register_existing_run_has_no_pid_and_is_running(db_connection, tmp_path):
    project = Q.create_pm_project(db_connection, name="RegP")
    team = Q.create_team(db_connection, name="RegT")
    log_dir = tmp_path / ".ai-pipeline" / "20260825-184848"
    log_dir.mkdir(parents=True)

    task = Q.register_existing_run(
        db_connection, pm_project_id=project["id"], team_id=team["id"],
        title="razor1911-demo-tribute", repo_path=str(tmp_path),
        run_id="20260825-184848",
    )
    assert task["pid"] is None
    assert task["status"] == "running"
    assert task["log_dir"] == str(log_dir)
