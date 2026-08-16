"""Provider narrows Find, and inherits through the conversation join."""

from __future__ import annotations

import pytest

from throughline.queries import find as F

pytestmark = pytest.mark.integration


@pytest.fixture()
def corpus(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("""
            INSERT INTO conversations
                (session_id, project_path, source_tool, started_at, message_count, summary)
            VALUES (gen_random_uuid(), '/p', 'claude_code', now(), 1, 'zebrafish study'),
                   (gen_random_uuid(), '/p', 'hermes',      now(), 1, 'zebrafish study'),
                   (gen_random_uuid(), '/p', NULL,          now(), 1, 'zebrafish study')
            """)
    db_connection.commit()
    return db_connection


def test_unfiltered_finds_all_three(corpus):
    res = F.find(corpus, "zebrafish", filters=F.FindFilters(kinds=["conversation"]), limit=50)
    assert len(res.items) >= 3


def test_one_provider_narrows(corpus):
    res = F.find(
        corpus,
        "zebrafish",
        filters=F.FindFilters(kinds=["conversation"], providers=["hermes"]),
        limit=50,
    )
    assert len(res.items) == 1


def test_several_providers_union(corpus):
    res = F.find(
        corpus,
        "zebrafish",
        filters=F.FindFilters(kinds=["conversation"], providers=["hermes", "claude_code"]),
        limit=50,
    )
    assert len(res.items) == 2


def test_browse_honours_the_filter(corpus):
    res = F.browse(corpus, F.FindFilters(kinds=["conversation"], providers=["hermes"]), limit=50)
    assert len(res.items) == 1


def test_messages_inherit_provider_through_their_conversation(corpus):
    """Spec §3.4: no denormalisation; the join already exists."""
    with corpus.cursor() as cur:
        cur.execute("SELECT id FROM conversations WHERE source_tool='hermes' LIMIT 1")
        conv_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (%s, 'user', 'zebrafish in the message', now())",
            (conv_id,),
        )
    corpus.commit()
    res = F.find(
        corpus,
        "zebrafish",
        filters=F.FindFilters(kinds=["message"], providers=["hermes"]),
        limit=50,
    )
    assert len(res.items) == 1


def test_memory_chunk_source_id_only_means_conversation_when_source_type_matches(corpus):
    """``memory_chunks.source_id`` is polymorphic: no FK, no CHECK constraint.
    It means "a conversation id" only when source_type='conversation'. A row
    of a different source_type whose source_id deliberately collides with a
    real hermes conversation id must NOT be attributed to hermes — that
    collision is exactly what an unguarded EXISTS would get wrong."""
    with corpus.cursor() as cur:
        cur.execute("SELECT id FROM conversations WHERE source_tool='hermes' LIMIT 1")
        hermes_conv_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO memory_chunks (source_type, source_id, content, category) "
            "VALUES ('manual', %s, 'zebrafish manual note', 'insight')",
            (hermes_conv_id,),
        )
    corpus.commit()
    res = F.find(
        corpus,
        "zebrafish",
        filters=F.FindFilters(kinds=["memory"], providers=["hermes"]),
        limit=50,
    )
    assert res.items == []
