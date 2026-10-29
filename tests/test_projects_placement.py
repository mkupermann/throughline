"""A working directory is not always a project.

`project_name` is the last path segment, which is right for
`~/Documents/GitHub/some-repo` and wrong for a session started from the home
directory — that one listed as a project named after the account. Measured on
a real corpus: nine of twelve names were genuine repositories, and the
exceptions were `~` and `/tmp`.

The fix is a rule about paths, not a config file: Throughline's claim is that
it works on what is already there, and a registry someone has to maintain is a
different product.
"""

from __future__ import annotations

import os

import pytest

from throughline.queries.projects import UNPLACED, is_placed


@pytest.mark.parametrize(
    "path",
    [
        "/Users/someone/Documents/GitHub/some-repo",
        "/opt/work/api",
        "/Users/someone/tmp",  # a `tmp` INSIDE a project is still that project
    ],
)
def test_a_real_working_directory_is_a_project(path):
    assert is_placed(path) is True


@pytest.mark.parametrize("path", ["/tmp", "/tmp/", "/private/tmp", "/var/tmp", "/", None, ""])
def test_scratch_locations_are_not_projects(path):
    assert is_placed(path) is False


def test_the_home_directory_is_not_a_project():
    """The case that produced a project named after the account holder."""
    assert is_placed(os.path.expanduser("~")) is False
    assert is_placed(os.path.expanduser("~") + "/") is False


def test_a_repository_called_tmp_keeps_its_name():
    """Matched by path, not by name — otherwise a legitimate project loses its
    identity to a heuristic about a word."""
    assert is_placed("/Users/someone/Documents/GitHub/tmp") is True


def test_unplaced_sessions_are_named_not_hidden():
    """They are real work that happened nowhere in particular; dropping them
    would lose sessions, and calling them "unknown" says less than it could."""
    assert UNPLACED.strip()
    assert "project" in UNPLACED.lower()
