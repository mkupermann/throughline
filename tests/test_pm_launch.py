from pathlib import Path

import pytest

from throughline.jobs import pm_launch


def test_windows_bash_resolution_skips_the_wsl_relay(tmp_path, monkeypatch):
    git_root = tmp_path / "Git"
    git = git_root / "cmd" / "git.exe"
    git_bash = git_root / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    git_bash.parent.mkdir(parents=True)
    git.write_bytes(b"")
    git_bash.write_bytes(b"")

    def fake_which(name: str) -> str | None:
        if name == "bash":
            return r"C:\Windows\System32\bash.exe"
        if name == "git":
            return str(git)
        return None

    monkeypatch.setattr(pm_launch.sys, "platform", "win32")
    monkeypatch.setattr(pm_launch.shutil, "which", fake_which)

    assert Path(pm_launch._resolve_bash_executable()) == git_bash


def test_windows_bash_resolution_refuses_the_wsl_relay_when_git_bash_is_missing(tmp_path, monkeypatch):
    def fake_which(name: str) -> str | None:
        if name == "bash":
            return r"C:\Windows\System32\bash.exe"
        return None

    monkeypatch.setattr(pm_launch.sys, "platform", "win32")
    monkeypatch.setattr(pm_launch.shutil, "which", fake_which)
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / name))

    assert pm_launch._resolve_bash_executable() is None


def test_launch_explains_that_git_bash_is_required(monkeypatch):
    monkeypatch.setattr(pm_launch, "BASH_EXECUTABLE", None)

    with pytest.raises(RuntimeError, match="Git Bash"):
        pm_launch.launch_task(
            None,
            pm_project_id=1,
            team_id=2,
            title="demo",
            repo_path="C:/work/demo",
        )
