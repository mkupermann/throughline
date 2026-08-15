"""DB-free tests for ``scripts/backfill_projects.py``.

The script's pure helpers (``collect_observed_names``,
``existing_project_names``, ``insert_missing``) take a connection-like
object, so we feed them an in-memory fake. The point is to verify the
scoping rules (sources include/exclude conversations) and the
idempotence semantics (existing names are skipped) without touching
Postgres — the unit-tests CI job has no DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


# ── Fakes ────────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, *, mc_names: list[str], conv_names: list[str],
                 existing: list[str]):
        self.mc_names = mc_names
        self.conv_names = conv_names
        self.existing = existing
        self.executemany_calls: list[tuple[str, list]] = []
        self._buf: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        s = " ".join(sql.split())
        if "DISTINCT project_name FROM public.memory_chunks" in s:
            self._buf = [(n,) for n in self.mc_names]
        elif "DISTINCT project_name FROM public.conversations" in s:
            self._buf = [(n,) for n in self.conv_names]
        elif "SELECT name FROM public.projects" in s:
            self._buf = [(n,) for n in self.existing]
        elif "SELECT count(*) FROM public.projects" in s:
            self._buf = [(len(self.existing),)]
        else:
            self._buf = []

    def executemany(self, sql: str, seq):
        self.executemany_calls.append((sql, list(seq)))

    def fetchall(self):
        return list(self._buf)

    def fetchone(self):
        return self._buf[0] if self._buf else None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.committed = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed += 1

    def close(self):
        pass


# ── Tests ────────────────────────────────────────────────────────────────────
def test_collect_observed_default_uses_memory_chunks_only():
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(
        mc_names=["alpha", "beta", "gamma"],
        conv_names=["delta", "epsilon"],
        existing=[],
    )
    names = bp.collect_observed_names(_FakeConn(cur), include_conversations=False)
    assert names == ["alpha", "beta", "gamma"]


def test_collect_observed_with_conversations_unions_dedups_and_sorts():
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(
        mc_names=["alpha", "beta", "gamma"],
        conv_names=["beta", "delta"],   # overlap on "beta"
        existing=[],
    )
    names = bp.collect_observed_names(_FakeConn(cur), include_conversations=True)
    assert names == ["alpha", "beta", "delta", "gamma"]


def test_existing_project_names_returns_a_set():
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(mc_names=[], conv_names=[], existing=["proj-x", "proj-y"])
    out = bp.existing_project_names(_FakeConn(cur))
    assert out == {"proj-x", "proj-y"}


def test_insert_missing_executes_with_active_status_and_commits():
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(mc_names=[], conv_names=[], existing=[])
    conn = _FakeConn(cur)
    n = bp.insert_missing(conn, ["alpha", "beta"])

    assert n == 2
    assert conn.committed == 1
    assert len(cur.executemany_calls) == 1
    sql, rows = cur.executemany_calls[0]
    assert "INSERT INTO public.projects" in sql
    assert "ON CONFLICT (name) DO NOTHING" in sql
    assert rows == [("alpha",), ("beta",)]


def test_insert_missing_noop_on_empty_list():
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(mc_names=[], conv_names=[], existing=[])
    conn = _FakeConn(cur)
    assert bp.insert_missing(conn, []) == 0
    assert conn.committed == 0
    assert cur.executemany_calls == []


def test_main_dry_run_does_not_write(monkeypatch, capsys):
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(
        mc_names=["alpha", "beta"],
        conv_names=[],
        existing=["alpha"],
    )
    conn = _FakeConn(cur)
    monkeypatch.setattr(bp, "psycopg2", type("P", (), {"connect": lambda **kw: conn}))

    rc = bp.main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "to insert: " in out
    assert "+ beta" in out
    assert "no writes" in out
    # No INSERT, no commit.
    assert cur.executemany_calls == []
    assert conn.committed == 0


def test_main_real_run_inserts_and_commits(monkeypatch, capsys):
    from throughline.jobs import backfill_projects as bp

    cur = _FakeCursor(
        mc_names=["alpha", "beta"],
        conv_names=[],
        existing=["alpha"],
    )
    conn = _FakeConn(cur)
    monkeypatch.setattr(bp, "psycopg2", type("P", (), {"connect": lambda **kw: conn}))

    rc = bp.main([])
    assert rc == 0
    out = capsys.readouterr().out
    # Only "beta" was missing.
    assert "to insert:                        1" in out
    assert "inserted 1 row" in out
    sql, rows = cur.executemany_calls[0]
    assert rows == [("beta",)]
    assert conn.committed == 1


def test_main_db_connect_failure_returns_1(monkeypatch, capsys):
    from throughline.jobs import backfill_projects as bp

    def _raise(**_kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(bp, "psycopg2", type("P", (), {"connect": _raise}))

    rc = bp.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "DB connect failed" in err
