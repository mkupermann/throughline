"""Nothing reaches a public repository just because it was in the directory.

An agent run with its working directory in this checkout created a folder here
for an unrelated project. It was untracked and not ignored, so `git add -A`
would have staged it and a push would have published it. None of the
configured pre-commit hooks look at whether a file belongs to this project at
all — they check formatting, size and private keys.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from scripts.check_toplevel import ALLOWED, stray_entries

ROOT = Path(__file__).resolve().parents[1]


def test_a_file_inside_a_known_directory_is_fine():
    assert stray_entries(["throughline/llm.py", "tests/test_llm.py", "docs/USAGE.md"]) == []


def test_a_known_top_level_file_is_fine():
    assert stray_entries(["README.md", "Makefile", "pyproject.toml"]) == []


def test_the_intentional_windows_support_directory_is_fine():
    assert stray_entries(["windows/register-tasks.ps1", "windows/README.md"]) == []


def test_a_foreign_project_folder_is_refused():
    strays = stray_entries(["canoscan-projekt/scan8600.py", "canoscan-projekt/README.md"])
    assert strays == ["canoscan-projekt"]


def test_each_stray_is_named_once_however_many_files_it_holds():
    strays = stray_entries([f"scratch/file{i}.py" for i in range(20)])
    assert strays == ["scratch"]


def test_a_stray_file_at_the_root_is_refused_too():
    assert stray_entries(["notes.md"]) == ["notes.md"]


def test_the_allowlist_covers_everything_this_repository_tracks():
    # A hook that fires on the project's own files gets disabled within a day.
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    assert stray_entries(tracked) == []


def test_tracked_symlinks_never_escape_the_repository():
    tracked = subprocess.run(["git", "ls-files", "-s"], capture_output=True, text=True, check=True).stdout.splitlines()
    violations: list[str] = []

    for entry in tracked:
        metadata, path = entry.split("\t", 1)
        if metadata.split()[0] != "120000":
            continue
        target = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True, check=True).stdout.strip()
        if PurePosixPath(target).is_absolute() or PureWindowsPath(target).is_absolute():
            violations.append(path)
            continue
        resolved = (ROOT / path).parent.joinpath(target).resolve()
        if not resolved.is_relative_to(ROOT.resolve()) or not resolved.exists():
            violations.append(path)

    assert violations == []


@pytest.mark.parametrize("path", ["", "   "])
def test_blank_lines_are_ignored(path):
    assert stray_entries([path]) == []


def test_the_allowlist_is_not_empty():
    assert len(ALLOWED) > 10
