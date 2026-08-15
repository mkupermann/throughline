"""Tests for ``throughline.status`` and the ``throughline status`` CLI.

All DB-free: a fake connection / cursor stands in for psycopg2 so we can
exercise the SQL paths without standing up Postgres. The shape and field
names of the payload are part of the contract — three other surfaces
(CLI, MCP tool, GUI card) consume it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_KEYS = {
    "db_reachable",
    "captured_at",
    "schema_version",
    "error",
    "table_row_counts",
    "chunks_total",
    "chunks_by_category",
    "embedding_coverage_pct",
    "last_extraction_at",
    "last_reflection_at",
    "contradictions_outstanding",
    "projects_count",
    "last_audit_at",
    "last_audit_sampled",
    "last_audit_drifted",
    "version",
}


# ── Drift-count parser ──────────────────────────────────────────────────────
class TestParseDriftCount:
    """Pin the auditor's reasoning-string → drift-count contract.

    ``status.collect_status()`` derives ``last_audit_drifted`` by regex-
    parsing the ``reasoning`` text the auditor writes. Both surfaces are
    in this repo, so the contract should be stable — a test guarantees it.
    """

    def test_zero_when_action_is_no_drift(self):
        from throughline.status import _parse_drift_count
        assert _parse_drift_count("Sampled 20 chunks, mean recall 0.99, threshold 0.3, 0 drifted.",
                                  "no_drift_detected") == 0

    def test_pulls_count_for_flagged_runs(self):
        from throughline.status import _parse_drift_count
        assert _parse_drift_count(
            "Sampled 20 chunks, mean recall 0.62, threshold 0.3, 4 drifted.",
            "flagged_drift",
        ) == 4

    def test_zero_when_reasoning_missing(self):
        from throughline.status import _parse_drift_count
        assert _parse_drift_count(None, "flagged_drift") == 0
        assert _parse_drift_count("", "flagged_drift") == 0

    def test_zero_when_format_drifts(self):
        # Future writers might drop the integer — that's fine, just don't crash.
        from throughline.status import _parse_drift_count
        assert _parse_drift_count("Audit complete.", "flagged_drift") == 0


# ── Fakes ────────────────────────────────────────────────────────────────────
class _FakeCursor:
    """Minimal cursor that returns canned answers per SQL pattern."""

    def __init__(
        self,
        *,
        counts: dict[str, int],
        chunks_total: int = 100,
        embedded: int = 80,
        contradictions: int = 3,
        projects: int = 4,
        last_reflection: datetime | None = None,
        last_extraction: datetime | None = None,
        schema_version_table_exists: bool = False,
        schema_version_value: str | None = None,
    ):
        self.counts = counts
        self.chunks_total = chunks_total
        self.embedded = embedded
        self.contradictions = contradictions
        self.projects = projects
        self.last_reflection = last_reflection
        self.last_extraction = last_extraction
        self.schema_version_table_exists = schema_version_table_exists
        self.schema_version_value = schema_version_value
        self._buf: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        s = " ".join(sql.split())
        if "to_regclass('public.schema_migrations')" in s:
            self._buf = [(self.schema_version_table_exists,)]
        elif "FROM public.schema_migrations" in s:
            self._buf = [(self.schema_version_value,)] if self.schema_version_value else []
        elif "WHERE reflection_type = 'contradiction'" in s:
            # Must come BEFORE the generic count-from-public branch — both
            # match this query otherwise.
            self._buf = [(self.contradictions,)]
        elif s.startswith("SELECT count(*) FROM public.") and "WHERE" not in s:
            tbl = s.split("FROM public.")[1].split()[0]
            self._buf = [(self.counts.get(tbl, 0),)]
        elif "FROM public.memory_chunks WHERE status = 'active' GROUP BY category" in s:
            self._buf = [("decision", 5), ("pattern", 7), ("workflow", 3)]
        elif "(SELECT count(*) FROM public.memory_chunks) AS chunks" in s:
            self._buf = [(self.chunks_total, self.embedded)]
        elif "max(created_at) FROM public.memory_chunks" in s:
            self._buf = [(self.last_extraction,)]
        elif "max(created_at) FROM public.memory_reflections" in s:
            self._buf = [(self.last_reflection,)]
        elif "DISTINCT project_name" in s:
            self._buf = [(self.projects,)]
        else:
            self._buf = []

    def fetchone(self):
        return self._buf[0] if self._buf else None

    def fetchall(self):
        return list(self._buf)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):  # pragma: no cover - never asserted
        pass


# ── Tests ────────────────────────────────────────────────────────────────────
def test_collect_status_db_unreachable_returns_safe_payload(monkeypatch):
    from throughline import status as st

    monkeypatch.setattr(st, "_connect", lambda: None)
    payload = st.collect_status()

    assert payload["db_reachable"] is False
    assert payload["error"]
    assert REQUIRED_KEYS <= set(payload), f"missing keys: {REQUIRED_KEYS - set(payload)}"
    # Even unreachable, the empty payload must be JSON-serialisable.
    json.dumps(payload)


def test_status_remedy_uses_the_packaged_migration_command():
    from throughline.status import format_human

    report = format_human(
        {
            "db_reachable": True,
            "pending_migrations": ["001_example.sql"],
            "table_row_counts": {},
        }
    )

    assert "throughline migrate" in report
    assert "scripts/migrate.py" not in report


def test_collect_status_with_fake_conn_populates_fields():
    from throughline import status as st

    last_refl = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    cur = _FakeCursor(
        counts={
            "conversations": 10,
            "messages": 50,
            "memory_chunks": 100,
            "embeddings": 80,
            "memory_reflections": 5,
            "skills": 20,
            "prompts": 8,
            "projects": 3,
            "entities": 40,
            "entity_mentions": 60,
            "relationships": 30,
        },
        chunks_total=100,
        embedded=80,
        contradictions=2,
        projects=3,
        last_reflection=last_refl,
    )
    conn = _FakeConn(cur)
    payload = st.collect_status(conn=conn)

    assert payload["db_reachable"] is True
    assert payload["table_row_counts"]["memory_chunks"] == 100
    assert payload["chunks_total"] == 100
    assert payload["embedding_coverage_pct"] == 80.0
    assert payload["chunks_by_category"]["decision"] == 5
    # Categories not returned by GROUP BY must still be present at zero.
    assert payload["chunks_by_category"]["insight"] == 0
    assert payload["last_reflection_at"] == last_refl.isoformat()
    assert payload["contradictions_outstanding"] == 2
    assert payload["projects_count"] == 3


def test_collect_status_handles_zero_chunks_without_dividing():
    from throughline import status as st

    cur = _FakeCursor(
        counts={
            t: 0
            for t in (
                "conversations",
                "messages",
                "memory_chunks",
                "embeddings",
                "memory_reflections",
                "skills",
                "prompts",
                "projects",
                "entities",
                "entity_mentions",
                "relationships",
            )
        },
        chunks_total=0,
        embedded=0,
    )
    payload = st.collect_status(conn=_FakeConn(cur))
    assert payload["embedding_coverage_pct"] == 0.0


def test_format_human_renders_unreachable():
    from throughline.status import _empty_payload, format_human  # type: ignore

    out = format_human(_empty_payload(error="boom"))
    assert "unreachable" in out.lower()
    assert "boom" in out


def test_format_human_renders_reachable_payload():
    from throughline.status import format_human

    payload = {
        "db_reachable": True,
        "captured_at": "2026-05-10T00:00:00+00:00",
        "schema_version": None,
        "error": None,
        "table_row_counts": {"memory_chunks": 42},
        "chunks_total": 42,
        "chunks_by_category": {"decision": 10, "pattern": 32},
        "embedding_coverage_pct": 75.0,
        "last_extraction_at": None,
        "last_reflection_at": None,
        "contradictions_outstanding": 1,
        "projects_count": 2,
        "version": "0.2.0",
    }
    out = format_human(payload)
    assert "reachable" in out
    assert "42" in out
    assert "75.00" in out
    assert "decision" in out


def test_cli_status_json_contract(tmp_path):
    """``python -m throughline status --json`` must emit valid JSON whose
    shape matches REQUIRED_KEYS, even with no DB reachable. CI relies on
    this."""
    env = {
        "PGHOST": "127.0.0.1",
        "PGPORT": "1",
        "PGUSER": "nobody",
        "PGCONNECT_TIMEOUT": "1",
        # Pass through PATH so subprocess can find python on macOS.
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "throughline", "status", "--json"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert REQUIRED_KEYS <= set(payload), f"missing keys: {REQUIRED_KEYS - set(payload)}"


def test_cli_status_pretty_indents_output(tmp_path):
    env = {
        "PGHOST": "127.0.0.1",
        "PGPORT": "1",
        "PGUSER": "nobody",
        "PGCONNECT_TIMEOUT": "1",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "throughline", "status", "--json", "--pretty"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # Pretty-printed has at least one indented line.
    assert "\n  " in proc.stdout
