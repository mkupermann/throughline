"""Tests for the offline / dry-run paths of the eval harness.

CI cannot stand up Postgres or pay for tokens; these tests prove the
harness is still useful in that environment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL_PY = REPO / "evals" / "run_eval.py"
QUESTIONS = REPO / "evals" / "questions.jsonl"


# Force PG to look unreachable so any retrieval attempt fails fast and
# proves the harness doesn't need a DB in these modes.
NO_DB_ENV = {
    "PGHOST": "127.0.0.1",
    "PGPORT": "1",
    "PGUSER": "nobody",
    "PGCONNECT_TIMEOUT": "1",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _run_eval(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EVAL_PY), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**NO_DB_ENV, "PYTHONPATH": str(REPO)},
        timeout=timeout,
    )


def test_dry_run_parses_30_questions_and_exits_zero():
    proc = _run_eval("--dry-run", "--questions", str(QUESTIONS))
    assert proc.returncode == 0, proc.stderr
    # All 30 questions emit a "would retrieve" line in dry-run.
    assert proc.stdout.count("would retrieve") == 30


def test_dry_run_does_not_write_a_report(tmp_path):
    report = tmp_path / "report.md"
    proc = _run_eval("--dry-run", "--report", str(report), "--questions", str(QUESTIONS))
    assert proc.returncode == 0, proc.stderr
    assert not report.exists(), "dry-run must not write a report"


def test_offline_stub_writes_report_and_perfect_with_memory(tmp_path):
    report = tmp_path / "report.md"
    proc = _run_eval("--offline-stub", "--report", str(report), "--questions", str(QUESTIONS), timeout=60)
    assert proc.returncode == 0, proc.stderr
    body = report.read_text(encoding="utf-8")
    assert "with-memory recall" in body
    assert "30/30" in body, "stub answers always hit"
    assert "0/30" in body, "stub cold answers always miss"
    # Per-question table renders.
    assert "| ID | Category" in body


def test_dry_run_and_offline_stub_are_mutually_exclusive():
    proc = _run_eval("--dry-run", "--offline-stub")
    assert proc.returncode == 2
    assert "mutually exclusive" in proc.stderr


def test_offline_stub_handles_missing_expected_substrings(tmp_path, monkeypatch):
    """If a question has an empty expected_substrings list, stub falls
    back to the I-do-not-know answer in both conditions — the harness
    must not crash on edge-case questions."""
    qfile = tmp_path / "edge.jsonl"
    qfile.write_text(
        '{"id":"E01","category":"control","question":"will Claude know this?","expected_substrings":[]}\n',
        encoding="utf-8",
    )
    report = tmp_path / "edge.md"
    proc = _run_eval(
        "--offline-stub",
        "--questions",
        str(qfile),
        "--report",
        str(report),
    )
    assert proc.returncode == 0, proc.stderr
    body = report.read_text(encoding="utf-8")
    assert "0/1" in body  # neither condition can hit on empty substrings
