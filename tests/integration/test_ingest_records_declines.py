"""A declined file must be recorded, so `pending` can reach zero.

`pending` is "discovered files with no ingestion_log row". Files the writer
deliberately declines — an empty transcript, or one recognised as Throughline's
own `claude -p` call — used to be skipped without a log entry, so they stayed
pending forever and were re-parsed on every run. The provider chip showed a
permanent amber count that no ingest could clear.

These tests pin the two decline paths and the one property that matters: run the
ingester twice over a corpus that is entirely declinable, and nothing is left
pending.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from throughline.adapters.base import Adapter, NormalisedConversation, NormalisedMessage
from throughline.adapters.writer import run_adapter

pytestmark = pytest.mark.integration


class _StubAdapter(Adapter):
    """Yields files from a directory; parses each according to its content.

    A file whose text is "EMPTY" parses to None (the parse-returned-nothing
    path). Anything else becomes a conversation whose single user message is
    that text — which lets a test drive the self-referential path by writing a
    prompt the real filter recognises.
    """

    name = "stub"
    label = "Stub"

    def __init__(self, home: Path) -> None:
        self.home = home

    def discover(self) -> Iterable[Path]:
        yield from sorted(self.home.glob("*.txt"))

    def parse(self, path: Path) -> NormalisedConversation | None:
        text = path.read_text(encoding="utf-8")
        if text == "EMPTY":
            return None
        return NormalisedConversation(
            session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, path.name)),
            project_path="/tmp/stub",
            model=None,
            entrypoint=None,
            started_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
            ended_at=None,
            messages=[NormalisedMessage(role="user", content=text)],
        )


def _pending(conn, paths: list[Path]) -> int:
    """Discovered files with no ingestion_log row — the provider-bar definition."""
    with conn.cursor() as cur:
        cur.execute("SELECT file_path FROM ingestion_log")
        logged = {r[0] for r in cur.fetchall()}
    return len([p for p in paths if str(p) not in logged])


def test_unparseable_file_is_logged_not_left_pending(db_env, tmp_path):
    (tmp_path / "empty.txt").write_text("EMPTY", encoding="utf-8")
    adapter = _StubAdapter(tmp_path)
    conn = psycopg2.connect(**db_env)
    try:
        run_adapter(adapter, conn=conn, verbose=False)
        assert _pending(conn, list(adapter.discover())) == 0, (
            "a file that parsed to nothing was left pending — it will be "
            "re-parsed on every run and the provider chip can never clear"
        )
        with conn.cursor() as cur:
            cur.execute("SELECT record_count FROM ingestion_log")
            assert cur.fetchone()[0] == 0, "a decline must be recorded as 0 records"
    finally:
        conn.close()


def test_self_referential_file_is_logged_not_left_pending(db_env, tmp_path):
    """The prompt text here must be one the real filter recognises."""
    from throughline.self_referential import self_referential_reason

    prompt = "Du bekommst einen Auszug aus einer Claude Code Session. Generiere einen Titel"
    assert self_referential_reason(prompt), (
        "test fixture is stale: the filter no longer recognises this prompt, so "
        "this test would pass for the wrong reason"
    )

    (tmp_path / "selfref.txt").write_text(prompt, encoding="utf-8")
    adapter = _StubAdapter(tmp_path)
    conn = psycopg2.connect(**db_env)
    try:
        run_adapter(adapter, conn=conn, verbose=False)
        assert _pending(conn, list(adapter.discover())) == 0
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM conversations")
            assert cur.fetchone()[0] == 0, "a self-referential file must not be stored"
    finally:
        conn.close()


def test_second_run_reparses_nothing(db_env, tmp_path):
    """The decline must persist across runs, not be re-decided every time."""
    (tmp_path / "empty.txt").write_text("EMPTY", encoding="utf-8")
    adapter = _StubAdapter(tmp_path)
    conn = psycopg2.connect(**db_env)
    try:
        run_adapter(adapter, conn=conn, verbose=False)
        second = run_adapter(adapter, conn=conn, verbose=False)
        # The hash matched an existing log row, so the writer skipped before
        # parsing at all — that is the whole point of recording the decision.
        assert second.skipped == 1
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ingestion_log")
            assert cur.fetchone()[0] == 1, "the decline was logged twice"
    finally:
        conn.close()


def test_a_growing_file_is_judged_again(db_env, tmp_path):
    """A new hash must get a fresh decision, or a declined file is written off.

    A transcript that is empty when first seen and real ten minutes later is the
    normal case for a live session, so the decline has to be keyed to content.
    """
    f = tmp_path / "grows.txt"
    f.write_text("EMPTY", encoding="utf-8")
    adapter = _StubAdapter(tmp_path)
    conn = psycopg2.connect(**db_env)
    try:
        run_adapter(adapter, conn=conn, verbose=False)
        f.write_text("a real question about postgres", encoding="utf-8")
        run_adapter(adapter, conn=conn, verbose=False)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM conversations")
            assert cur.fetchone()[0] == 1, "the file grew into a real conversation but was never re-judged"
    finally:
        conn.close()
