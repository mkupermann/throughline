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

import threading
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from throughline.adapters import writer
from throughline.adapters.base import Adapter, NormalisedConversation, NormalisedMessage
from throughline.jobs import generate_embeddings

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


class _RefreshAdapter(Adapter):
    """One mutable transcript whose second version is invalid for Postgres."""

    name = "refresh-rollback"
    label = "Refresh rollback fixture"

    def __init__(self, path: Path, session_id: str):
        self.home = path.parent
        self.path = path
        self.session_id = session_id

    def discover(self):
        return [self.path]

    def parse(self, path: Path):
        conv = _conversation(1, self.session_id)
        if path.read_text(encoding="utf-8") == "invalid":
            conv.messages[0].role = "not-a-message-role"
        return conv


class _EmbeddingBackend:
    model = "refresh-lock-test"
    column = "embedding_768"


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


def test_refresh_does_not_remove_other_conversations_derived_rows(db_connection):
    """Refreshing one transcript must only remove its message-derived records."""
    refreshed_id, _ = _write(db_connection, _conversation(1, "77777777-7777-7777-7777-777777777777"))
    untouched_id, _ = _write(db_connection, _conversation(1, "88888888-8888-8888-8888-888888888888"))
    with db_connection.cursor() as cur:
        cur.execute("SELECT id FROM messages WHERE conversation_id = %s", (refreshed_id,))
        refreshed_message_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM messages WHERE conversation_id = %s", (untouched_id,))
        untouched_message_id = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO embeddings (source_type, source_id) VALUES ('message', %s)",
            [(refreshed_message_id,), (untouched_message_id,)],
        )
        cur.executemany(
            "INSERT INTO entity_mentions (source_type, source_id) VALUES ('conversation', %s)",
            [(refreshed_id,), (untouched_id,)],
        )
    db_connection.commit()

    _write(db_connection, _conversation(1, "77777777-7777-7777-7777-777777777777"))

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM embeddings WHERE source_id = %s", (refreshed_message_id,))
        refreshed_embeddings = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM entity_mentions WHERE source_type = 'conversation' AND source_id = %s",
            (refreshed_id,),
        )
        refreshed_mentions = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM embeddings WHERE source_id = %s", (untouched_message_id,))
        untouched_embeddings = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM entity_mentions WHERE source_type = 'conversation' AND source_id = %s",
            (untouched_id,),
        )
        untouched_mentions = cur.fetchone()[0]

    assert refreshed_embeddings == 0
    assert refreshed_mentions == 0
    assert untouched_embeddings == 1
    assert untouched_mentions == 1


def test_embedding_producer_cannot_write_old_message_after_refresh(test_db):
    """A producer that read an old id must revalidate it after a refresh commits."""
    sid = "99999999-9999-9999-9999-999999999999"
    setup = psycopg2.connect(**test_db)
    refresh = psycopg2.connect(**test_db)
    producer = psycopg2.connect(**test_db)
    try:
        old_conv_id, _ = _write(setup, _conversation(1, sid))
        with setup.cursor() as cur:
            cur.execute("SELECT id FROM messages WHERE conversation_id = %s", (old_conv_id,))
            old_message_id = cur.fetchone()[0]

        producer_read_old_id = threading.Event()
        refresh_committed = threading.Event()
        producer_done = threading.Event()
        producer_result: dict[str, object] = {}

        def write_stale_embedding() -> None:
            try:
                with producer.cursor() as cur:
                    cur.execute("SELECT id FROM messages WHERE id = %s", (old_message_id,))
                    assert cur.fetchone() == (old_message_id,)
                    producer_read_old_id.set()
                    assert refresh_committed.wait(timeout=5)
                    producer_result["inserted"] = generate_embeddings.upsert_embedding(
                        cur, _EmbeddingBackend(), "message", old_message_id, [0.0] * 768
                    )
                producer.commit()
            except BaseException as exc:  # surfaced in the main test thread
                producer.rollback()
                producer_result["error"] = exc
            finally:
                producer_done.set()

        thread = threading.Thread(target=write_stale_embedding)
        thread.start()
        assert producer_read_old_id.wait(timeout=5)
        _write(refresh, _conversation(1, sid))
        refresh_committed.set()
        assert producer_done.wait(timeout=5)
        thread.join(timeout=1)

        assert "error" not in producer_result
        assert producer_result["inserted"] is False
        with setup.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM embeddings WHERE source_type = 'message' AND source_id = %s",
                (old_message_id,),
            )
            assert cur.fetchone()[0] == 0
    finally:
        setup.close()
        refresh.close()
        producer.close()


def test_run_adapter_rolls_back_refresh_and_ingestion_log_after_late_failure(tmp_path, db_connection):
    """A failed message insert must restore prior rows and retain only the old ingest log."""
    sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    path = tmp_path / "session.json"
    path.write_text("valid", encoding="utf-8")
    adapter = _RefreshAdapter(path, sid)
    first = writer.run_adapter(adapter, conn=db_connection, verbose=False)
    assert first.ingested == 1

    with db_connection.cursor() as cur:
        cur.execute("SELECT id FROM conversations WHERE session_id = %s", (sid,))
        conversation_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM messages WHERE conversation_id = %s", (conversation_id,))
        old_message_id = cur.fetchone()[0]
        cur.execute("INSERT INTO embeddings (source_type, source_id) VALUES ('message', %s)", (old_message_id,))
        cur.execute(
            "INSERT INTO entity_mentions (source_type, source_id) VALUES ('conversation', %s)",
            (conversation_id,),
        )
    db_connection.commit()

    path.write_text("invalid", encoding="utf-8")
    failed = writer.run_adapter(adapter, conn=db_connection, verbose=False)

    assert failed.errors == 1
    with db_connection.cursor() as cur:
        cur.execute("SELECT id FROM messages WHERE conversation_id = %s", (conversation_id,))
        assert cur.fetchone() == (old_message_id,)
        cur.execute(
            "SELECT count(*) FROM embeddings WHERE source_type = 'message' AND source_id = %s",
            (old_message_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM entity_mentions WHERE source_type = 'conversation' AND source_id = %s",
            (conversation_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM ingestion_log WHERE file_path = %s", (str(path),))
        assert cur.fetchone()[0] == 1
