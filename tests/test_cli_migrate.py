"""Installed migration command contract."""

from __future__ import annotations

import sys


def test_migrate_subcommand_dispatches_to_packaged_job(monkeypatch) -> None:
    from throughline import cli

    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: {})
    monkeypatch.setattr(
        cli,
        "_call_script_main",
        lambda module, argv: calls.append((module, argv)) or 0,
    )

    assert cli.main(["migrate", "--dry-run"]) == 0
    assert calls == [("migrate", ["--dry-run"])]


def test_shell_commands_receive_the_running_throughline_interpreter(monkeypatch) -> None:
    from throughline import cli

    observed: dict = {}
    monkeypatch.setattr(cli.subprocess, "call", lambda _cmd, **kwargs: observed.update(kwargs) or 0)

    assert cli._run_shell_script("install_hooks.sh", []) == 0
    assert observed["env"]["THROUGHLINE_PYTHON"] == sys.executable
