"""The migration creates every pm_* table with the columns later tasks rely on.

Not a business-logic test — a guard so a typo in the migration (a renamed
column, a forgotten NOT NULL) is caught here instead of surfacing as an
opaque psycopg2 error three tasks later.
"""
import pytest

PM_TABLES = {
    "pm_roles": {"id", "name", "description", "default_ai_tool", "default_ai_model",
                 "skill_refs", "instructions", "document_refs", "token_budget"},
    "pm_members": {"id", "name", "member_type", "contact_info", "skill_refs",
                    "instructions", "document_refs", "token_budget"},
    "pm_teams": {"id", "name", "description", "token_budget"},
    "pm_projects": {"id", "name", "description", "status", "token_budget"},
    "pm_project_repos": {"pm_project_id", "project_id"},
    "pm_project_teams": {"pm_project_id", "team_id"},
    "pm_team_roles": {"team_id", "role_id"},
    "pm_assignments": {"id", "pm_project_id", "team_id", "role_id", "member_id",
                        "ai_tool", "ai_model"},
    "pm_tasks": {"id", "pm_project_id", "team_id", "title", "status", "run_id",
                 "repo_path", "log_dir", "pid", "tokens_used"},
    "pm_task_events": {"id", "task_id", "assignment_id", "step", "iteration",
                        "event_type", "message", "detail_path", "tokens_used"},
    "pm_ai_providers": {"id", "name", "provider_type", "base_url", "api_key",
                         "custom_models", "enabled"},
}


@pytest.mark.integration
def test_pm_tables_have_expected_columns(db_connection):
    with db_connection.cursor() as cur:
        for table, expected_cols in PM_TABLES.items():
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            actual = {row[0] for row in cur.fetchall()}
            assert expected_cols <= actual, f"{table} missing {expected_cols - actual}"


@pytest.mark.integration
def test_pm_member_type_check_constraint(db_connection):
    with db_connection.cursor() as cur:
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO pm_members (name, member_type) VALUES (%s, %s)",
                ("bad", "not-a-real-type"),
            )
    db_connection.rollback()


@pytest.mark.integration
def test_pm_ai_providers_provider_type_check_constraint(db_connection):
    with db_connection.cursor() as cur:
        with pytest.raises(Exception):
            cur.execute(
                "INSERT INTO pm_ai_providers (name, provider_type) VALUES (%s, %s)",
                ("bad", "not-a-real-provider"),
            )
    db_connection.rollback()
