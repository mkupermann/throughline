"""Manual membership survives the real import writer and stays consistent across views."""

import uuid
from datetime import datetime, timezone

import pytest

from throughline.adapters import writer
from throughline.adapters.base import NormalisedConversation
from throughline.api.routers.story import Checkpoint
from throughline.queries import assignments, projects, story
from throughline.queries._exec import one

pytestmark = pytest.mark.integration


def conversation(conn, path="/work/ambiguous"):
    value = NormalisedConversation(
        session_id=str(uuid.uuid4()),
        project_path=path,
        model=None,
        entrypoint=None,
        started_at=datetime.now(timezone.utc),
        ended_at=None,
        messages=[],
    )
    with conn.cursor() as cur:
        cid = writer._upsert_conversation(cur, value)
    conn.commit()
    return cid, value


def test_assignment_survives_reimport_and_clear_restores_current_source(db_connection):
    conn = db_connection
    cid, conv = conversation(conn)
    key = assignments.create_project(conn, "Research platform")["project"]
    one(
        conn,
        "INSERT INTO memory_chunks(source_type,source_id,content,category,project_name) VALUES ('conversation',%s,'A test result','insight','ambiguous')",
        (cid,),
    )
    conn.commit()
    assignments.assign(conn, [cid], key, "ambiguous")
    conv.project_path = "C:\\work\\renamed-folder"
    with conn.cursor() as cur:
        assert writer._upsert_conversation(cur, conv) == cid
    conn.commit()
    row = one(
        conn,
        "SELECT project_name,source_project_name,project_path,assigned_project FROM conversations WHERE id=%s",
        (cid,),
    )
    assert row == {
        "project_name": key,
        "assigned_project": key,
        "source_project_name": "renamed-folder",
        "project_path": conv.project_path,
    }
    assert one(conn, "SELECT project_name FROM memory_chunks WHERE source_id=%s", (cid,))["project_name"] == key
    assert [r["id"] for r in projects.sessions(conn, key)] == [cid]
    assert projects.sessions(conn, "ambiguous") == []
    assignments.assign(conn, [cid], None, key)
    assert one(conn, "SELECT project_name FROM conversations WHERE id=%s", (cid,))["project_name"] == "renamed-folder"
    assert len(assignments.history(conn, cid)) == 2


def test_assignment_conflict_is_atomic_and_unplaced_conversation_can_join_a_project(db_connection):
    conn = db_connection
    first, _ = conversation(conn, "/tmp")
    second, _ = conversation(conn, "/work/another")
    key = assignments.create_project(conn, "Meaningful project")["project"]
    with pytest.raises(ValueError, match="membership changed"):
        assignments.assign(conn, [first, second], key, projects.UNPLACED)
    conn.rollback()
    assert one(conn, "SELECT assigned_project FROM conversations WHERE id=%s", (first,))["assigned_project"] is None
    assignments.assign(conn, [first], key, projects.UNPLACED)
    assert projects.recent(conn, days=None)[0]["project"] in {key, "another"}
    assert [r["id"] for r in projects.sessions(conn, key)] == [first]


def test_earlier_state_keeps_its_evidence_and_flags_moved_source(db_connection):
    conn = db_connection
    cid, _ = conversation(conn)
    story.checkpoint(conn, "ambiguous", Checkpoint(kind="goal", content="Investigate retrieval", conversation_id=cid))
    key = assignments.create_project(conn, "Confirmed research")["project"]
    assignments.assign(conn, [cid], key, "ambiguous")
    old = story.history(conn, "ambiguous")
    assert old["checkpoints"][0]["source_available"] is True
    assert old["checkpoints"][0]["source_in_scope"] is False
    assert old["checkpoints"][0]["content"] == "Investigate retrieval"
    assert story.history(conn, key)["checkpoints"] == []
