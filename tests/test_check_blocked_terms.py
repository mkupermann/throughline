"""The blocked-terms check finds listed terms without ever printing them."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_blocked_terms",
    Path(__file__).resolve().parents[1] / "scripts" / "check_blocked_terms.py",
)
cbt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cbt)


def test_terms_skip_blanks_and_comments_and_lowercase():
    terms = cbt.load_terms({"BLOCKED_TERMS": "# customers\nAcme\n\n  Globex Corp  \n"})
    assert terms == ["acme", "globex corp"]


def test_terms_come_from_a_file_when_the_variable_is_empty(tmp_path):
    f = tmp_path / "terms.txt"
    f.write_text("Initech\n", encoding="utf-8")
    assert cbt.load_terms({"BLOCKED_TERMS": "", "BLOCKED_TERMS_FILE": str(f)}) == ["initech"]


def test_a_term_matches_only_as_a_whole_word():
    patterns = cbt.compile_terms(["acme", "a.b"])
    items = [
        ("hit-upper", "Deploy for ACME tomorrow"),
        ("hit-dash", "acme-ca.crt"),
        ("hit-dot", "A.B-Netz"),
        ("miss-inside", "macme and acmes"),
        ("miss-dot", "aXb"),
    ]
    assert cbt.find(patterns, items) == [("hit-upper", 1), ("hit-dash", 1), ("hit-dot", 2)]


def test_added_lines_carry_path_and_line_number():
    diff = (
        "diff --git a/x.txt b/x.txt\n"
        "--- a/x.txt\n"
        "+++ b/x.txt\n"
        "@@ -3,0 +4,2 @@\n"
        "+first\n"
        "+second\n"
        "@@ -9 +11 @@\n"
        "-old\n"
        "+third\n"
        "--- a/gone.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-deleted\n"
    )
    assert cbt.added_lines(diff) == [
        ("x.txt", "x.txt"),
        ("x.txt:4", "first"),
        ("x.txt:5", "second"),
        ("x.txt:11", "third"),
    ]


def test_commit_messages_are_split_per_commit():
    log = "abc1234\x00feat: a\n\nbody\n\x1edef5678\x00fix: b\n\x1e"
    assert cbt.commit_messages(log) == [("commit abc1234", "feat: a\n\nbody\n"), ("commit def5678", "fix: b\n")]


def test_no_terms_fails_unless_allowed():
    assert cbt.main(["x", "HEAD"], {}) == 2
    assert cbt.main(["x", "HEAD"], {"ALLOW_EMPTY_TERMS": "1"}) == 0


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_range_with_a_term_fails_and_never_prints_it(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore: start")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("hello\nship it to Globex tomorrow\n", encoding="utf-8")
    (repo / "notes-initech.md").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "docs: clean subject")
    monkeypatch.chdir(repo)

    env = {"BLOCKED_TERMS": "globex\ninitech\n", "PR_BODY": "Ready for globex review"}
    assert cbt.main(["x", f"{base}..HEAD"], env) == 1
    out = capsys.readouterr()
    report = out.out + out.err
    assert "a.txt:2: blocked term #1" in report
    assert "notes-initech.md: blocked term #2" in report
    assert "pr body: blocked term #1" in report
    assert "globex" not in report.lower()
    assert "initech" not in report.lower().replace("notes-initech.md", "")

    assert cbt.main(["x", f"{base}..HEAD"], {"BLOCKED_TERMS": "umbrella\n"}) == 0
