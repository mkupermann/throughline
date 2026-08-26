from pathlib import Path

from throughline.jobs.pm_watch import (
    extract_aider_tokens,
    latest_iteration,
    parse_spec,
    parse_verdict,
    read_run_text,
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
    these, or the watcher's backfill crashes every tick. And now that the
    decoder tries cp1252 before falling back to replacement, the umlaut
    itself must come through correctly rather than as U+FFFD (mojibake)."""
    (tmp_path / "verdict-3.txt").write_bytes(
        b"Pr\xfcfung fehlgeschlagen.\n\nVERDICT: FAIL: Umlaut-Test"
    )
    status, message = parse_verdict(tmp_path, 3)
    assert status == "fail"
    assert "Umlaut-Test" in message
    assert "Prüfung" in message
    assert "�" not in message


# ── read_run_text: the UTF-8 → cp1252 → replace decoder chain ──────────────


def test_read_run_text_cp1252_umlauts_decode_correctly(tmp_path: Path):
    """cp1252 bytes that are invalid UTF-8 (an umlaut here) must decode to
    the real character, not U+FFFD — this is the whole point of trying
    cp1252 before falling back to errors="replace"."""
    f = tmp_path / "cp1252.txt"
    f.write_bytes("Prüfung bestanden – Änderungen übernommen".encode("cp1252"))
    text = read_run_text(f)
    assert text == "Prüfung bestanden – Änderungen übernommen"
    assert "�" not in text


def test_read_run_text_valid_utf8_is_exact(tmp_path: Path):
    """A well-formed UTF-8 file must round-trip exactly — the cp1252
    fallback must never be tried when UTF-8 already succeeds."""
    f = tmp_path / "utf8.txt"
    original = "Prüfung bestanden — Ünïcödé"
    f.write_bytes(original.encode("utf-8"))
    assert read_run_text(f) == original


def test_read_run_text_mixed_garbage_falls_back_to_replace(tmp_path: Path):
    """Bytes that are valid in neither UTF-8 nor cp1252 (cp1252 leaves a
    handful of codepoints, e.g. 0x81/0x8D/0x8F/0x90/0x9D, undefined) must
    not raise — the decoder falls back to UTF-8 with errors="replace"."""
    f = tmp_path / "garbage.txt"
    f.write_bytes(b"before \x81\x8d\x8f\x90\x9d after")
    text = read_run_text(f)  # must not raise
    assert "before " in text
    assert " after" in text
    assert "�" in text


def test_extract_aider_tokens_sums_all_turns():
    log = "some output\nTokens: 3.4k sent, 130 received.\nmore output\nTokens: 500 sent, 40 received.\n"
    # 3.4k -> 3400 + 130 + 500 + 40
    assert extract_aider_tokens(log) == 3400 + 130 + 500 + 40


def test_extract_aider_tokens_no_matches_is_zero():
    assert extract_aider_tokens("no token lines here") == 0
