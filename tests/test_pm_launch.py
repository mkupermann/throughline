from pathlib import Path

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
