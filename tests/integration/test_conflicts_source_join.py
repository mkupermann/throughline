"""Pin the memory_chunks -> conversations join to source_id, not via messages.

``memory_chunks.source_id`` IS a ``conversations.id`` when
``source_type = 'conversation'`` (see ``scripts/extract_memory.py``, which
inserts ``source_type='conversation', source_id=conv_id``). conflicts.py used
to reach conversations by treating ``source_id`` as a *messages* id instead:

    JOIN messages m ON m.id = mc.source_id JOIN conversations c ON c.id = m.conversation_id

Both ``conversations`` and ``messages`` have their own independently
incrementing bigserial id sequences, so a memory_chunk's ``source_id`` (a
conversation id) can coincide with an unrelated *message*'s id. The wrong
join doesn't fail in that case — it silently resolves to whatever
conversation that message belongs to, attributing the chunk to the wrong
provider, "succeeding" instead of returning nothing.
"""

from __future__ import annotations

import pytest

from throughline import conflicts

pytestmark = pytest.mark.integration


@pytest.fixture()
def colliding_ids(db_connection):
    """Four conversations (providers A, B, D, C) plus a supersession pair
    (a on A, b on C) whose ``source_id``s each collide with an unrelated
    message belonging to a different conversation (B, D respectively).

    Fresh test DB: conversations_id_seq and messages_id_seq both start at 1
    and each conversation/message insert here consumes exactly one id, so
    ids are deterministic: conv_a=1, conv_b=2, conv_d=3, conv_c=4, and the
    four messages inserted afterwards get ids 1..4 in insertion order. That
    makes msg#1 (belonging to conv_b) collide with conv_a's id, and msg#4
    (belonging to conv_d) collide with conv_c's id — the collisions the join
    bug depends on.
    """
    with db_connection.cursor() as cur:
        conv_ids = {}
        for key, entrypoint, provider in [
            ("a", "cli", "claude_code"),        # id 1 — the superseded chunk's real conversation
            ("b", "windsurf", "windsurf"),       # id 2 — decoy: owns the message colliding with conv_a
            ("d", "hermes", "hermes"),           # id 3 — decoy: owns the message colliding with conv_c
            ("c", "codex", "codex"),             # id 4 — the superseding chunk's real conversation
        ]:
            cur.execute(
                """
                INSERT INTO conversations (session_id, project_path, entrypoint, source_tool, started_at, message_count)
                VALUES (gen_random_uuid(), '/repo/x', %s, %s, now(), 1)
                RETURNING id
                """,
                (entrypoint, provider),
            )
            conv_ids[key] = cur.fetchone()[0]

        # Messages 1..4, in an order that makes msg#1 belong to conv_b and
        # msg#4 belong to conv_d — the two collisions.
        msg_owner_order = ["b", "b", "b", "d"]
        msg_ids = []
        for owner in msg_owner_order:
            cur.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at)
                VALUES (%s, 'assistant', 'irrelevant', now())
                RETURNING id
                """,
                (conv_ids[owner],),
            )
            msg_ids.append(cur.fetchone()[0])

        assert msg_ids[0] == conv_ids["a"], (
            "test assumption broken: expected msg#1's id to collide with "
            f"conv_a's id ({conv_ids['a']}), got {msg_ids[0]}. "
            "conversations_id_seq / messages_id_seq no longer start in lockstep."
        )
        assert msg_ids[3] == conv_ids["c"], (
            "test assumption broken: expected msg#4's id to collide with "
            f"conv_c's id ({conv_ids['c']}), got {msg_ids[3]}."
        )

        # The superseding chunk (b side of the Conflict): source_id = conv_c's
        # id, which also collides with msg#4 (belonging to conv_d / hermes).
        # Correct join -> codex. Buggy join (via messages) -> hermes.
        cur.execute(
            """
            INSERT INTO memory_chunks (source_type, source_id, content, category, project_name, status)
            VALUES ('conversation', %s, 'new decision', 'decision', 'proj', 'active')
            RETURNING id
            """,
            (conv_ids["c"],),
        )
        chunk_b = cur.fetchone()[0]

        # The superseded chunk (a side): source_id = conv_a's id, which also
        # collides with msg#1 (belonging to conv_b / windsurf).
        # Correct join -> claude_code. Buggy join (via messages) -> windsurf.
        cur.execute(
            """
            INSERT INTO memory_chunks
                (source_type, source_id, content, category, project_name, status, superseded_by, superseded_at)
            VALUES ('conversation', %s, 'old decision', 'decision', 'proj', 'superseded', %s, now())
            RETURNING id
            """,
            (conv_ids["a"], chunk_b),
        )
    db_connection.commit()
    return db_connection


def test_supersession_attributes_chunks_via_source_id_not_a_colliding_message(colliding_ids):
    """Both sides of the conflict must resolve via source_id directly.

    Under the old messages-based join this doesn't return zero rows — it
    returns one row with both tools wrong (windsurf, hermes instead of
    claude_code, codex), which is the dangerous failure mode: a plausible,
    silently incorrect answer.
    """
    with colliding_ids.cursor() as cur:
        found = conflicts._supersession_conflicts(cur, project=None)
    assert len(found) == 1, f"expected exactly one supersession conflict, got {len(found)}"
    conflict = found[0]
    assert conflict.a.tool == "claude_code", (
        f"chunk should be attributed to its own conversation (claude_code) via "
        f"source_id, not to a colliding message's conversation; got {conflict.a.tool!r}"
    )
    assert conflict.b.tool == "codex", (
        f"superseding chunk should be attributed to codex via source_id, "
        f"not to a colliding message's conversation; got {conflict.b.tool!r}"
    )
