"""Unit tests for scripts/audit_extraction.py.

These cover the pure-Python audit machinery (token recall, drift
threshold, summary aggregation) without standing up the DB. The
``run_audit`` integration path is exercised by mocking a connection
whose cursor replays canned rows, so the SQL contract is pinned without
needing a live Postgres.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from throughline.jobs import audit_extraction as ae


class TestMeaningfulWords:
    def test_basic_tokenisation(self):
        out = ae.meaningful_words("pgvector cosine distance over memory chunks")
        # All ≥4-char content words present.
        assert {"pgvector", "cosine", "distance", "over", "memory", "chunks"} <= out

    def test_short_tokens_dropped(self):
        # < 4 chars → dropped (catches noise like 'is', 'an', 'I').
        out = ae.meaningful_words("a is an the of for")
        assert out == set()

    def test_stopwords_dropped(self):
        out = ae.meaningful_words("the AND for with these those have")
        # All are in the stoplist.
        assert out == set()

    def test_case_insensitive(self):
        out = ae.meaningful_words("PGVector pgvector PgVector")
        assert out == {"pgvector"}

    def test_returns_set_not_list(self):
        # Duplicates collapse, downstream set-intersection works.
        out = ae.meaningful_words("repeat repeat repeat unique")
        assert out == {"repeat", "unique"}


class TestAuditChunk:
    def test_full_overlap_recall_one(self):
        recall, drifted, reason = ae.audit_chunk(
            chunk_content="pgvector cosine distance memory chunks",
            source_text="We chose pgvector for cosine distance over memory chunks. Notes about indexing.",
        )
        assert recall == 1.0
        assert drifted is False
        assert reason == ""

    def test_zero_overlap_drifted(self):
        recall, drifted, reason = ae.audit_chunk(
            chunk_content="weather sunny tomorrow morning",
            source_text="discussion about pgvector cosine indexing memory chunks",
        )
        assert recall == 0.0
        assert drifted is True
        assert reason == "low_recall"

    def test_vacuous_chunk_not_flagged(self):
        # A chunk with no meaningful words (very short / only stopwords)
        # is vacuous — recall is 1.0 by convention, drifted=False.
        recall, drifted, reason = ae.audit_chunk(
            chunk_content="a the of and",
            source_text="anything at all goes here",
        )
        assert recall == 1.0
        assert drifted is False
        assert reason == "vacuous"

    def test_no_source_is_drift(self):
        # If we couldn't find the source, the chunk is unverifiable —
        # treat as drift (operator needs to know to investigate).
        recall, drifted, reason = ae.audit_chunk(
            chunk_content="pgvector cosine distance",
            source_text="",
        )
        assert recall == 0.0
        assert drifted is True
        assert reason == "no_source"

    def test_threshold_respected(self):
        # 1 / 4 distinctive words match → recall 0.25.
        chunk = "pgvector hosting decision budget"
        source = "pgvector is great"  # only 'pgvector' overlaps with chunk
        recall, drifted_strict, _ = ae.audit_chunk(chunk, source, threshold=0.50)
        _, drifted_lax, _ = ae.audit_chunk(chunk, source, threshold=0.10)
        assert recall == pytest.approx(0.25)
        assert drifted_strict is True   # 0.25 < 0.50
        assert drifted_lax is False     # 0.25 >= 0.10

    def test_partial_overlap_above_threshold(self):
        chunk = "pgvector cosine distance memory"
        source = "we use pgvector for cosine search, and we like it"  # 2/4 → 0.5
        recall, drifted, _ = ae.audit_chunk(chunk, source, threshold=0.30)
        assert recall == pytest.approx(0.5)
        assert drifted is False


# -- run_audit integration with a mock conn ---------------------------------


@dataclass
class _FakeCursor:
    """Replays scripted rows in the order ``run_audit`` issues queries."""

    sample_rows: list[dict] = field(default_factory=list)
    message_rows_by_conv: dict[int, list[str]] = field(default_factory=dict)
    last_insert_id: int = 999
    log: list[tuple[str, tuple]] = field(default_factory=list)
    description: list = field(default_factory=list)
    _scripted_fetch: list = field(default_factory=list)

    def execute(self, sql: str, params: tuple = ()):
        self.log.append((sql, params))
        normalised = " ".join(sql.lower().split())
        if "from memory_chunks" in normalised and "order by random" in normalised:
            class _Col:
                def __init__(self, name): self.name = name
            self.description = [_Col(n) for n in (
                "id", "source_type", "source_id", "category", "content", "project_name"
            )]
            self._scripted_fetch = [tuple(r.values()) for r in self.sample_rows]
        elif "from messages" in normalised:
            conv_id = params[0] if params else None
            msgs = self.message_rows_by_conv.get(conv_id, [])
            self._scripted_fetch = [(m,) for m in msgs]
        elif "insert into memory_reflections" in normalised:
            self._scripted_fetch = [(self.last_insert_id,)]
        else:
            self._scripted_fetch = []

    def fetchall(self):
        out, self._scripted_fetch = self._scripted_fetch, []
        return out

    def fetchone(self):
        if self._scripted_fetch:
            return self._scripted_fetch.pop(0)
        return None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self.cur = cursor
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def cursor(self):
        yield self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _chunk(cid, content, source_id=100, cat="insight"):
    return {
        "id": cid,
        "source_type": "conversation",
        "source_id": source_id,
        "category": cat,
        "content": content,
        "project_name": "throughline",
    }


class TestRunAudit:
    def test_records_audit_row_when_writing(self):
        cur = _FakeCursor(
            sample_rows=[_chunk(1, "pgvector cosine memory chunks")],
            message_rows_by_conv={100: ["we picked pgvector for cosine memory chunks today"]},
        )
        conn = _FakeConn(cur)
        summary = ae.run_audit(conn, limit=1, threshold=0.3, write_audit_row=True)
        assert summary["sampled"] == 1
        assert summary["drifted"] == 0
        assert summary["drift_rate"] == 0.0
        assert summary["mean_recall"] == pytest.approx(1.0)
        assert summary["reflection_id"] == 999
        # The INSERT was logged.
        sqls = [entry[0] for entry in cur.log]
        assert any("INSERT INTO memory_reflections" in s for s in sqls)
        assert conn.committed is True

    def test_dry_run_does_not_write(self):
        cur = _FakeCursor(
            sample_rows=[_chunk(1, "weather sunny rain forecast")],
            message_rows_by_conv={100: ["unrelated discussion of pgvector"]},
        )
        conn = _FakeConn(cur)
        summary = ae.run_audit(conn, limit=1, threshold=0.3, write_audit_row=False)
        assert summary["drifted"] == 1
        assert summary["reflection_id"] is None
        # No INSERT in the log.
        sqls = [entry[0] for entry in cur.log]
        assert not any("INSERT INTO memory_reflections" in s for s in sqls)
        assert conn.committed is False

    def test_drift_aggregates_per_category(self):
        cur = _FakeCursor(
            sample_rows=[
                _chunk(1, "matching content alpha beta", source_id=10, cat="pattern"),
                _chunk(2, "totally unrelated zulu yankee", source_id=11, cat="pattern"),
                _chunk(3, "matching insight echo foxtrot", source_id=12, cat="insight"),
            ],
            message_rows_by_conv={
                10: ["matching content alpha beta arrives here"],
                11: ["nothing to see here, totally different terms"],
                12: ["echo and foxtrot show up in this insight"],
            },
        )
        conn = _FakeConn(cur)
        summary = ae.run_audit(conn, limit=3, threshold=0.5, write_audit_row=False)
        assert summary["sampled"] == 3
        assert summary["drifted"] == 1
        assert summary["by_category"] == {
            "pattern": {"sampled": 2, "drifted": 1},
            "insight": {"sampled": 1, "drifted": 0},
        }
        assert summary["drifted_ids"] == [2]

    def test_empty_sample_short_circuits(self):
        cur = _FakeCursor(sample_rows=[])
        conn = _FakeConn(cur)
        summary = ae.run_audit(conn, limit=10, write_audit_row=True)
        assert summary["sampled"] == 0
        assert summary["drifted"] == 0
        assert summary["mean_recall"] == 1.0
        # Don't write an audit row for a zero-sample run; nothing to record.
        sqls = [entry[0] for entry in cur.log]
        assert not any("INSERT INTO memory_reflections" in s for s in sqls)
