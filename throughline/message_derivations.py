"""Coordinate refreshes with jobs that derive rows from conversation messages.

Rows such as message embeddings do not have database foreign keys to their
source messages. A refresh replaces those messages, so every participant takes
the same transaction-scoped advisory lock for the owning conversation before
it deletes or writes a derivation.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_LOCK_PREFIX = "throughline:message-derivation:"


def lock_conversations(cursor: Any, conversation_ids: Iterable[int]) -> None:
    """Acquire conversation locks in a global order until transaction end."""
    for conversation_id in sorted({int(conversation_id) for conversation_id in conversation_ids}):
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{_LOCK_PREFIX}{conversation_id}",),
        )


def lock_and_revalidate_messages(cursor: Any, message_ids: Iterable[int]) -> set[int]:
    """Lock each owning conversation, then return message ids still present."""
    ids = sorted({int(message_id) for message_id in message_ids})
    if not ids:
        return set()

    cursor.execute("SELECT DISTINCT conversation_id FROM messages WHERE id = ANY(%s)", (ids,))
    lock_conversations(cursor, (row[0] for row in cursor.fetchall()))

    cursor.execute("SELECT id FROM messages WHERE id = ANY(%s)", (ids,))
    return {row[0] for row in cursor.fetchall()}


def lock_and_revalidate_conversation(cursor: Any, conversation_id: int) -> bool:
    """Lock one conversation and confirm it still exists under that lock."""
    lock_conversations(cursor, [conversation_id])
    cursor.execute("SELECT 1 FROM conversations WHERE id = %s", (conversation_id,))
    return cursor.fetchone() is not None
