"""Guards for the batched message writer.

`_replace_messages` switched from one `execute()` per message to a single
`execute_values()` (~5x faster on loopback Postgres). The risks that
introduces are not about speed:

* a batch spanning more than one `page_size` chunk must still write every row,
* the JSONB columns must survive adaptation in bulk exactly as they did
  per-row,
* replace semantics (DELETE then INSERT) must stay idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from throughline.adapters import writer
from throughline.adapters.base import NormalisedConversation, NormalisedMessage

pytestmark = pytest.mark.integration

#: Deliberately larger than writer._MESSAGE_BATCH_SIZE so execute_values has
#: to emit more than one statement — an off-by-one in paging would show here
#: and nowhere else.
BIG = writer._MESSAGE_BATCH_SIZE * 2 + 37


def _conversation(n_messages: int, session_id: str) -> NormalisedConversation:
    return NormalisedConversation(
        session_id=session_id,
        project_path="/repo/batching",
        model="claude",
        entrypoint="claude-code",
        git_branch="main",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        summary="batching fixture",
        metadata={"fixture": True},
        messages=[
            NormalisedMessage(
                uuid=None,
                parent_uuid=None,
                role="assistant" if i % 2 else "user",
                content=f"message {i}",
                content_blocks=[{"type": "text", "text": f"block {i}"}],
                tool_calls=[{"name": "Bash"}] if i % 7 == 0 else None,
                tool_name="Bash" if i % 7 == 0 else None,
                is_sidechain=(i % 11 == 0),
                model="claude",
                token_count=i,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                metadata={"i": i},
            )
            for i in range(n_messages)
        ],
    )


def _write(conn, conv) -> int:
    with conn.cursor() as cur:
        conv_id = writer._upsert_conversation(cur, conv)
        written = writer._replace_messages(cur, conv_id, conv)
    conn.commit()
    return conv_id, written


def test_writes_every_row_across_multiple_pages(db_connection):
    conv_id, written = _write(db_connection, _conversation(BIG, "11111111-1111-1111-1111-111111111111"))
    assert written == BIG
    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM messages WHERE conversation_id = %s", (conv_id,))
        assert cur.fetchone()[0] == BIG


def test_jsonb_columns_round_trip(db_connection):
    conv_id, _ = _write(db_connection, _conversation(20, "22222222-2222-2222-2222-222222222222"))
    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT content_blocks, tool_calls, metadata, is_sidechain, token_count
            FROM messages WHERE conversation_id = %s ORDER BY token_count
            """,
            (conv_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 20
    for i, (blocks, tool_calls, metadata, sidechain, tokens) in enumerate(rows):
        assert blocks == [{"type": "text", "text": f"block {i}"}]
        assert metadata == {"i": i}
        assert tool_calls == ([{"name": "Bash"}] if i % 7 == 0 else None)
        assert sidechain == (i % 11 == 0)
        assert tokens == i


def test_replace_is_idempotent(db_connection):
    sid = "33333333-3333-3333-3333-333333333333"
    conv_id, _ = _write(db_connection, _conversation(50, sid))
    conv_id2, written2 = _write(db_connection, _conversation(50, sid))

    assert conv_id2 == conv_id, "same session_id must upsert the same conversation"
    assert written2 == 50
    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM messages WHERE conversation_id = %s", (conv_id,))
        assert cur.fetchone()[0] == 50, "re-ingest duplicated messages"


def test_empty_message_list_clears_and_returns_zero(db_connection):
    sid = "44444444-4444-4444-4444-444444444444"
    conv_id, _ = _write(db_connection, _conversation(10, sid))
    conv_id2, written = _write(db_connection, _conversation(0, sid))

    assert conv_id2 == conv_id
    assert written == 0
    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM messages WHERE conversation_id = %s", (conv_id,))
        assert cur.fetchone()[0] == 0


def test_refresh_replaces_every_normalised_conversation_field(db_connection):
    """A changed transcript must clear stale values instead of preserving them."""
    sid = "55555555-5555-5555-5555-555555555555"
    original = _conversation(3, sid)
    original.token_count_in = 7
    original.token_count_out = 8
    original.source_tool = "initial-adapter"
    _write(db_connection, original)
    refreshed = NormalisedConversation(
        session_id=sid,
        project_path="/repo/refreshed",
        model=None,
        entrypoint="scheduled-import",
        git_branch=None,
        started_at=datetime(2026, 2, 3, 4, 5, tzinfo=timezone.utc),
        ended_at=None,
        messages=_conversation(1, sid).messages,
        token_count_in=123,
        token_count_out=None,
        summary=None,
        metadata={"current": "only"},
        source_tool=None,
    )

    conv_id, _ = _write(db_connection, refreshed)

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT project_path, model, entrypoint, git_branch, started_at, ended_at,
                   message_count, token_count_in, token_count_out, summary, metadata, source_tool
            FROM conversations WHERE id = %s
            """,
            (conv_id,),
        )
        row = cur.fetchone()

    assert row == (
        "/repo/refreshed",
        None,
        "scheduled-import",
        None,
        datetime(2026, 2, 3, 4, 5, tzinfo=timezone.utc),
        None,
        1,
        123,
        None,
        None,
        {"current": "only"},
        None,
    )


def test_message_replacement_removes_message_derived_rows(db_connection):
    """Embeddings and mentions for replaced message ids must not become orphans."""
    sid = "66666666-6666-6666-6666-666666666666"
    conv_id, _ = _write(db_connection, _conversation(2, sid))
    with db_connection.cursor() as cur:
        cur.execute("SELECT id FROM messages WHERE conversation_id = %s ORDER BY id", (conv_id,))
        old_message_ids = [row[0] for row in cur.fetchall()]
        cur.executemany(
            "INSERT INTO embeddings (source_type, source_id) VALUES ('message', %s)",
            [(message_id,) for message_id in old_message_ids],
        )
        cur.executemany(
            """
            INSERT INTO entity_mentions (source_type, source_id, context_snippet)
            VALUES ('message', %s, 'old message')
            """,
            [(message_id,) for message_id in old_message_ids],
        )
    db_connection.commit()

    _write(db_connection, _conversation(1, sid))

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM embeddings WHERE source_type = 'message' AND source_id = ANY(%s)",
            (old_message_ids,),
        )
        embedding_count = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM entity_mentions WHERE source_type = 'message' AND source_id = ANY(%s)",
            (old_message_ids,),
        )
        mention_count = cur.fetchone()[0]
        cur.execute("SELECT content FROM messages WHERE conversation_id = %s", (conv_id,))
        replacement_content = [row[0] for row in cur.fetchall()]

    assert embedding_count == 0
    assert mention_count == 0
    assert replacement_content == ["message 0"]
