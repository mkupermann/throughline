"""Unit tests for ``scripts/forget.py``.

The two cascade-delete primitives (``forget_chunks`` / ``forget_entity``)
run inside a single transaction, write an audit row to
``memory_reflections``, and commit on success / rollback on failure.

Rather than spin up a live DB for every test, we drive them with a
minimal mock connection that records the SQL it sees. Integration tests
(under ``tests/integration/``) hit the real DB for end-to-end safety.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from forget import forget_chunks, forget_entity  # noqa: E402  (scripts/ on sys.path via conftest)


@dataclass
class _FakeCursor:
    """Records each ``execute()`` call and replays canned ``fetchone()`` rows."""

    log: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    fetch_queue: list[Any] = field(default_factory=list)
    rowcount: int = 0

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.log.append((sql.strip(), params))
        # Approximate psycopg2's rowcount semantics for tests that care:
        # DELETE statements get a row count proportional to the id list size.
        normalised = " ".join(sql.lower().split())
        if normalised.startswith("delete from embeddings"):
            self.rowcount = self._len_of_id_array(params)
        elif normalised.startswith("delete from memory_chunks"):
            self.rowcount = self._len_of_id_array(params)
        elif normalised.startswith("delete from entities"):
            self.rowcount = 1
        else:
            self.rowcount = 0

    @staticmethod
    def _len_of_id_array(params: tuple) -> int:
        for p in params:
            if isinstance(p, list):
                return len(p)
        return 0

    def fetchone(self):
        if self.fetch_queue:
            return self.fetch_queue.pop(0)
        return None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, fetch_queue: list | None = None) -> None:
        self.cur = _FakeCursor(fetch_queue=list(fetch_queue or []))
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def cursor(self):
        yield self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _RaisingConn(_FakeConn):
    """Cursor blows up on the second execute() — exercises the rollback path."""

    def __init__(self, fetch_queue: list | None = None) -> None:
        super().__init__(fetch_queue=fetch_queue)
        self.calls = 0
        original = self.cur.execute

        def execute(sql, params=()):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated DB failure")
            original(sql, params)

        self.cur.execute = execute  # type: ignore[assignment]


class TestForgetChunks:
    def test_empty_id_list_is_a_noop(self):
        conn = _FakeConn()
        result = forget_chunks(conn, [], reason="never used")
        assert result == {"chunks": 0, "embeddings": 0, "reflection_id": None}
        assert conn.cur.log == []
        # Empty input must not touch the transaction either way.
        assert conn.committed is False
        assert conn.rolled_back is False

    def test_happy_path_emits_three_statements_then_audit(self):
        # The audit insert is the 4th statement; queue its RETURNING id.
        conn = _FakeConn(fetch_queue=[(99,)])
        result = forget_chunks(conn, [1, 2, 3], reason="duplicate seeds")

        sqls = [entry[0].split("\n")[0].strip() for entry in conn.cur.log]
        # 1. embeddings delete  2. supersede repair  3. chunk delete  4. audit insert
        assert len(sqls) == 4
        assert sqls[0].startswith("DELETE FROM embeddings")
        assert sqls[1].startswith("UPDATE memory_chunks SET superseded_by = NULL")
        assert sqls[2].startswith("DELETE FROM memory_chunks")
        assert "memory_reflections" in conn.cur.log[3][0]

        assert result["chunks"] == 3
        assert result["embeddings"] == 3
        assert result["reflection_id"] == 99
        assert conn.committed is True
        assert conn.rolled_back is False

    def test_reason_is_truncated_to_4000_chars(self):
        conn = _FakeConn(fetch_queue=[(1,)])
        long_reason = "x" * 10_000
        forget_chunks(conn, [42], reason=long_reason)
        audit_call = conn.cur.log[-1]
        # The reason is the second param after the id list.
        _ids, reason_param = audit_call[1]
        assert len(reason_param) == 4000

    def test_string_ids_are_coerced_to_int(self):
        conn = _FakeConn(fetch_queue=[(1,)])
        forget_chunks(conn, ["7", "8"], reason="r")
        # First call is the embeddings delete; second param is the id list.
        first_sql, first_params = conn.cur.log[0]
        id_list = first_params[0]
        assert id_list == [7, 8]
        assert all(isinstance(i, int) for i in id_list)

    def test_failure_rolls_back_and_reraises(self):
        conn = _RaisingConn(fetch_queue=[(1,)])
        with pytest.raises(RuntimeError, match="simulated"):
            forget_chunks(conn, [1, 2], reason="r")
        assert conn.rolled_back is True
        assert conn.committed is False


class TestForgetEntity:
    def test_happy_path_counts_mentions_and_relationships(self):
        # fetchone queue: SELECT mentions, SELECT relationships, RETURNING id
        conn = _FakeConn(fetch_queue=[(5,), (3,), (77,)])
        result = forget_entity(conn, 42, reason="merged duplicate")

        sqls = [entry[0].split("\n")[0].strip() for entry in conn.cur.log]
        assert any("FROM entity_mentions" in s for s in sqls)
        assert any("FROM relationships" in s for s in sqls)
        assert any("DELETE FROM entities" in s for s in sqls)
        # Audit row gets the entity id wrapped in a list.
        audit_params = conn.cur.log[-1][1]
        assert audit_params[0] == [42]

        assert result["entity"] == 1
        assert result["mentions"] == 5
        assert result["relationships"] == 3
        assert result["reflection_id"] == 77
        assert conn.committed is True

    def test_failure_rolls_back_and_reraises(self):
        conn = _RaisingConn(fetch_queue=[(0,), (0,), (1,)])
        with pytest.raises(RuntimeError, match="simulated"):
            forget_entity(conn, 9, reason="r")
        assert conn.rolled_back is True
        assert conn.committed is False
