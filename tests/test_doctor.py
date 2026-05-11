"""Tests for ``throughline.doctor``.

Doctor must never crash: every check catches its own exceptions, and
the report aggregates results. These tests verify the contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from throughline import doctor
from throughline.doctor import (
    CheckResult,
    DoctorReport,
    _ALL_CHECKS,
    check_python_version,
    check_required_packages,
    format_human,
    run_doctor,
)


def test_check_result_serializes() -> None:
    r = CheckResult(
        name="x", category="python", status="pass", message="ok"
    )
    d = r.to_dict()
    assert d["name"] == "x"
    assert d["status"] == "pass"
    assert d["category"] == "python"
    assert d["details"] == {}


def test_report_counts() -> None:
    rep = DoctorReport(checks=[
        CheckResult("a", "python", "pass", ""),
        CheckResult("b", "python", "warn", ""),
        CheckResult("c", "postgres", "fail", ""),
        CheckResult("d", "postgres", "fail", ""),
    ])
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
        assert c.category in {"python", "postgres", "adapters", "embeddings", "schedule"}


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
    rep = DoctorReport(checks=[
        CheckResult("ok", "python", "pass", "fine"),
        CheckResult("bad", "postgres", "fail", "broken", remedy="fix it"),
    ])
    txt = format_human(rep, color=False)
    assert "── python ──" in txt
    assert "── postgres ──" in txt
    assert "→ fix it" in txt  # remedy printed for non-pass
    assert "1 pass · 0 warn · 1 fail" in txt


def test_format_human_skips_remedy_for_pass() -> None:
    rep = DoctorReport(checks=[
        CheckResult("ok", "python", "pass", "fine", remedy="should not appear"),
    ])
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
