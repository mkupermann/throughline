from pathlib import Path

from throughline.jobs.pm_watch import (
    extract_aider_tokens,
    latest_iteration,
    parse_spec,
    parse_verdict,
)


def test_parse_spec_reads_file(tmp_path: Path):
    (tmp_path / "SPEC.md").write_text("## Ziel\nAdd a function.", encoding="utf-8")
    assert parse_spec(tmp_path) == "## Ziel\nAdd a function."


def test_parse_spec_missing_file_returns_none(tmp_path: Path):
    assert parse_spec(tmp_path) is None


def test_latest_iteration_finds_highest_n(tmp_path: Path):
    (tmp_path / "executor-1.log").write_text("x")
    (tmp_path / "executor-2.log").write_text("x")
    assert latest_iteration(tmp_path) == 2


def test_latest_iteration_no_files_is_zero(tmp_path: Path):
    assert latest_iteration(tmp_path) == 0


def test_parse_verdict_pass(tmp_path: Path):
    (tmp_path / "verdict-1.txt").write_text(
        "Alles erfuellt.\n\nVERDICT: PASS", encoding="utf-8"
    )
    status, message = parse_verdict(tmp_path, 1)
    assert status == "pass"
    assert "VERDICT: PASS" in message


def test_parse_verdict_fail(tmp_path: Path):
    (tmp_path / "verdict-2.txt").write_text(
        "Test schlaegt fehl.\n\nVERDICT: FAIL: assert fehlt", encoding="utf-8"
    )
    status, message = parse_verdict(tmp_path, 2)
    assert status == "fail"
    assert "assert fehlt" in message


def test_parse_verdict_not_yet_written(tmp_path: Path):
    assert parse_verdict(tmp_path, 5) is None


def test_parse_verdict_tolerates_cp1252_bytes(tmp_path: Path):
    """~19 of the real razor1911 verdict files are cp1252, not UTF-8 (an
    umlaut like the one in "Pruefung" below encodes to an invalid UTF-8
    byte sequence) — parse_verdict must not raise UnicodeDecodeError on
    these, or the watcher's backfill crashes every tick."""
    (tmp_path / "verdict-3.txt").write_bytes(
        b"Pr\xfcfung fehlgeschlagen.\n\nVERDICT: FAIL: Umlaut-Test"
    )
    status, message = parse_verdict(tmp_path, 3)
    assert status == "fail"
    assert "Umlaut-Test" in message


def test_extract_aider_tokens_sums_all_turns():
    log = "some output\nTokens: 3.4k sent, 130 received.\nmore output\nTokens: 500 sent, 40 received.\n"
    # 3.4k -> 3400 + 130 + 500 + 40
    assert extract_aider_tokens(log) == 3400 + 130 + 500 + 40


def test_extract_aider_tokens_no_matches_is_zero():
    assert extract_aider_tokens("no token lines here") == 0
