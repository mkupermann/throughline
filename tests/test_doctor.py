"""Tests for ``throughline.doctor``.

Doctor must never crash: every check catches its own exceptions, and
the report aggregates results. These tests verify the contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from throughline import doctor
from throughline.doctor import (
    _ALL_CHECKS,
    CheckResult,
    DoctorReport,
    check_python_version,
    check_required_packages,
    format_human,
    run_doctor,
)


def test_check_result_serializes() -> None:
    r = CheckResult(name="x", category="python", status="pass", message="ok")
    d = r.to_dict()
    assert d["name"] == "x"
    assert d["status"] == "pass"
    assert d["category"] == "python"
    assert d["details"] == {}


def test_report_counts() -> None:
    rep = DoctorReport(
        checks=[
            CheckResult("a", "python", "pass", ""),
            CheckResult("b", "python", "warn", ""),
            CheckResult("c", "postgres", "fail", ""),
            CheckResult("d", "postgres", "fail", ""),
        ]
    )
    assert rep.passes == 1
    assert rep.warns == 1
    assert rep.fails == 2
    assert rep.to_dict()["summary"] == {"pass": 1, "warn": 1, "fail": 2}


def test_python_version_check_passes_on_this_interpreter() -> None:
    r = check_python_version()
    assert r.status == "pass", r.message
    assert r.category == "python"
    assert r.details["current"].startswith(f"{sys.version_info.major}.")


def test_required_packages_check_runs() -> None:
    # We don't assert pass/fail — depends on the test environment — but it
    # must produce a CheckResult with a known shape and never raise.
    r = check_required_packages()
    assert r.name == "required_packages"
    assert r.status in {"pass", "fail"}
    if r.status == "fail":
        assert r.remedy and "pip install" in r.remedy


def test_run_doctor_returns_one_result_per_check() -> None:
    rep = run_doctor()
    assert len(rep.checks) == len(_ALL_CHECKS)
    # Every check has the contract fields.
    for c in rep.checks:
        assert c.name
        assert c.status in {"pass", "warn", "fail"}
        assert c.category in {"python", "postgres", "adapters", "embeddings", "schedule", "archive"}


def test_run_doctor_category_filter() -> None:
    rep = run_doctor(categories=["python"])
    assert rep.checks, "filter should still produce some checks"
    assert all(c.category == "python" for c in rep.checks)


def test_check_exception_is_captured_as_fail() -> None:
    """A check that raises must come back as FAIL, never crash the report."""

    @doctor._check("boom", "python")
    def boom() -> CheckResult:
        raise RuntimeError("kaboom")

    r = boom()
    assert r.status == "fail"
    assert "kaboom" in r.message
    assert r.name == "boom"


def test_format_human_renders_summary() -> None:
    rep = DoctorReport(
        checks=[
            CheckResult("ok", "python", "pass", "fine"),
            CheckResult("bad", "postgres", "fail", "broken", remedy="fix it"),
        ]
    )
    txt = format_human(rep, color=False)
    assert "── python ──" in txt
    assert "── postgres ──" in txt
    assert "→ fix it" in txt  # remedy printed for non-pass
    assert "1 pass · 0 warn · 1 fail" in txt


def test_format_human_skips_remedy_for_pass() -> None:
    rep = DoctorReport(
        checks=[
            CheckResult("ok", "python", "pass", "fine", remedy="should not appear"),
        ]
    )
    txt = format_human(rep, color=False)
    assert "should not appear" not in txt


def test_cli_doctor_json_is_valid(tmp_path: Path) -> None:
    """End-to-end smoke: `python -m throughline.cli doctor --json` returns valid JSON."""
    result = subprocess.run(
        [sys.executable, "-m", "throughline.cli", "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Exit code may be 0 or 1 depending on the test machine's state; we don't care.
    # We DO care that stdout parses.
    payload = json.loads(result.stdout)
    assert "checks" in payload
    assert "summary" in payload
    assert {"pass", "warn", "fail"} == set(payload["summary"].keys())
    # Category filter via CLI
    result = subprocess.run(
        [sys.executable, "-m", "throughline.cli", "doctor", "--json", "--category", "python"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    assert all(c["category"] == "python" for c in payload["checks"])


# --- archive category -------------------------------------------------------
#
# These checks exist because 91% of the transcripts this tool has ingested no
# longer exist on disk: for those, the database is the only copy, so it has to
# be checkable rather than merely present.


def test_format_human_renders_unknown_categories() -> None:
    """A new category must not vanish from the text report.

    The renderer walked a hardcoded list of five categories, so the entire
    `archive` category printed nothing while still counting toward the
    summary — the totals disagreed with the screen, and `--json` was the only
    way to see the checks at all. Silent omission is the worst failure mode a
    diagnostic can have.
    """
    rep = DoctorReport(
        checks=[
            CheckResult("known", "python", "pass", "fine"),
            CheckResult("newish", "archive", "warn", "something drifted", remedy="fix it"),
        ]
    )
    txt = format_human(rep, color=False)
    assert "── archive ──" in txt
    assert "something drifted" in txt
    assert "1 pass · 1 warn · 0 fail" in txt


def test_known_categories_keep_their_order() -> None:
    """Unknown categories append; they must not reshuffle the familiar ones."""
    rep = DoctorReport(
        checks=[
            CheckResult("z", "archive", "pass", "a"),
            CheckResult("a", "python", "pass", "b"),
            CheckResult("m", "postgres", "pass", "c"),
        ]
    )
    txt = format_human(rep, color=False)
    assert txt.index("── python ──") < txt.index("── postgres ──") < txt.index("── archive ──")


def test_backup_check_warns_when_directory_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_MEMORY_BACKUP_DIR", str(tmp_path / "nope"))
    r = doctor.check_archive_backup()
    assert r.status == "warn"
    assert "install_backup_agent" in (r.remedy or "")


def test_backup_check_ignores_empty_dumps(tmp_path, monkeypatch) -> None:
    """A zero-byte file is a failed dump, not a backup.

    An interrupted pg_dump leaves one behind, and counting it would report
    "backed up" at exactly the moment the backup stopped working.
    """
    monkeypatch.setenv("CLAUDE_MEMORY_BACKUP_DIR", str(tmp_path))
    (tmp_path / "claude_memory_2026-01-01_000000.sql.gz").write_bytes(b"")
    r = doctor.check_archive_backup()
    assert r.status == "warn"
    assert "no usable dump" in r.message


def test_backup_check_warns_on_a_stale_dump(tmp_path, monkeypatch) -> None:
    """Older than two daily runs means a run was actually missed."""
    import os
    import time as _time

    monkeypatch.setenv("CLAUDE_MEMORY_BACKUP_DIR", str(tmp_path))
    old = tmp_path / "claude_memory_2026-01-01_000000.sql.gz"
    old.write_bytes(b"not empty")
    stale = _time.time() - 5 * 24 * 3600
    os.utime(old, (stale, stale))

    r = doctor.check_archive_backup()
    assert r.status == "warn"
    assert "days old" in r.message
    assert r.details["age_hours"] > 48


def test_backup_check_passes_on_a_fresh_dump(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_MEMORY_BACKUP_DIR", str(tmp_path))
    (tmp_path / "claude_memory_2026-01-01_000000.sql.gz").write_bytes(b"x" * 2048)
    r = doctor.check_archive_backup()
    assert r.status == "pass"
    assert r.details["count"] == 1


# --------------------------------------------------------------------------- #
# The removed vendor CLI                                                      #
# --------------------------------------------------------------------------- #


def test_the_remedy_names_a_model_the_project_actually_defaults_to(monkeypatch):
    # A remedy telling people to pull a model the tool no longer prefers sends
    # them to a second-choice model and a second round of debugging.
    from throughline import doctor, llm

    monkeypatch.setattr(llm, "backend_info", lambda: llm.LLMInfo(False, detail="nothing here"))
    result = doctor.check_answer_backend()
    assert llm._DEFAULT_MODEL["ollama"] in (result.remedy or "")


def test_a_leftover_claude_setting_is_called_out(monkeypatch):
    # Someone whose generation ran through the claude CLI has CLAUDE_BIN or
    # THROUGHLINE_ANSWER_BACKEND=claude set. Silence here means they discover
    # the change as an unexplained failure.
    from throughline import doctor, llm

    monkeypatch.setenv("THROUGHLINE_ANSWER_BACKEND", "claude")
    monkeypatch.setattr(llm, "backend_info", lambda: llm.LLMInfo(False, detail="not a backend"))
    result = doctor.check_answer_backend()
    assert result.status == "warn"
    assert "claude" in (result.message + (result.remedy or "")).lower()
