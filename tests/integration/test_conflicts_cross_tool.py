"""Cross-tool conflict detection must not count Claude Code against itself.

Before source_tool, conflicts.py grouped by `entrypoint`, where Claude Code
writes both `cli` and `sdk-cli`. Two Claude Code sessions with different
invocation styles therefore looked like two different tools disagreeing —
the signature analysis of the product, reporting noise.
"""

from __future__ import annotations

import pytest

from throughline import conflicts

pytestmark = pytest.mark.integration


@pytest.fixture()
def two_claude_sessions(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations
                (session_id, project_path, entrypoint, source_tool, started_at, message_count)
            VALUES (gen_random_uuid(), '/repo/x', 'cli',     'claude_code', now(), 1),
                   (gen_random_uuid(), '/repo/x', 'sdk-cli', 'claude_code', now(), 1)
            RETURNING id
            """
        )
    db_connection.commit()
    return db_connection


def test_same_provider_different_entrypoints_is_not_cross_tool(two_claude_sessions):
    """Bar 7."""
    tools = conflicts.tools_in_use(two_claude_sessions)
    assert "cli" not in tools and "sdk-cli" not in tools
    assert tools == ["claude_code"] or tools == []


def test_entrypoint_values_are_untouched(two_claude_sessions):
    """Spec §8: the correction is in grouping, not in the data."""
    with two_claude_sessions.cursor() as cur:
        cur.execute("SELECT DISTINCT entrypoint FROM conversations WHERE project_path='/repo/x'")
        assert {r[0] for r in cur.fetchall()} == {"cli", "sdk-cli"}
