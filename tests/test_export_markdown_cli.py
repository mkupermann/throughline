"""How the exporter is invoked — from the command line and from the service.

The service must be able to name a destination without that destination ever
becoming a command-line argument: the job registry's whole guarantee is that
a request body cannot reach argv.
"""

from __future__ import annotations

import pytest

from throughline.jobs import export_markdown as em


def test_out_may_come_from_the_environment_instead_of_the_command_line(monkeypatch, tmp_path):
    monkeypatch.setenv(em.DEST_ENV, str(tmp_path / "vault"))
    assert em.destination_from(argv_out=None) == str(tmp_path / "vault")


def test_an_explicit_out_wins_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(em.DEST_ENV, str(tmp_path / "vom-env"))
    assert em.destination_from(argv_out=str(tmp_path / "explizit")) == str(tmp_path / "explizit")


def test_no_destination_anywhere_is_an_error(monkeypatch):
    monkeypatch.delenv(em.DEST_ENV, raising=False)
    with pytest.raises(ValueError, match="No destination"):
        em.destination_from(argv_out=None)


def test_the_cli_exports_into_the_directory_it_was_given(monkeypatch, tmp_path):
    # End to end through main(), with the database stubbed out.
    conv = {
        "id": 1,
        "session_id": "11111111-2222-3333-4444-555555555555",
        "project_name": "demo",
        "project_path": str(tmp_path),
        "model": "claude-opus-5",
        "git_branch": "main",
        "started_at": __import__("datetime").datetime(2026, 8, 13, 9, 0),
        "ended_at": None,
        "message_count": 1,
        "summary": "Ein Titel",
        "source_tool": "claude_code",
    }
    message = {
        "role": "user",
        "content": "Exportiere das.",
        "content_blocks": None,
        "tool_calls": None,
        "created_at": __import__("datetime").datetime(2026, 8, 13, 9, 0),
    }

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(em.psycopg2, "connect", lambda **kw: _Conn())
    monkeypatch.setattr(em, "fetch_conversations", lambda conn, **kw: [conv])
    monkeypatch.setattr(em, "fetch_messages", lambda conn, cid: [message])
    monkeypatch.setattr(em, "fetch_memory", lambda conn, name: [])
    monkeypatch.setattr("sys.argv", ["export-markdown", "--out", str(tmp_path / "vault")])

    assert em.main() == 0
    assert (tmp_path / "vault" / "README.md").exists()
    assert "Exportiere das." in (tmp_path / "vault" / "demo" / "demo.md").read_text(encoding="utf-8")


def test_every_option_can_arrive_through_the_environment(monkeypatch):
    monkeypatch.setenv(em.DEST_ENV, "/tmp/vault")
    monkeypatch.setenv("THROUGHLINE_EXPORT_PROJECT", "throughline")
    monkeypatch.setenv("THROUGHLINE_EXPORT_SINCE", "2026-01-01")
    monkeypatch.setenv("THROUGHLINE_EXPORT_INCLUDE_GENERATED", "1")
    monkeypatch.setenv("THROUGHLINE_EXPORT_REDACT", "1")
    monkeypatch.setenv("THROUGHLINE_EXPORT_TOOL_OUTPUT", "400")
    monkeypatch.setenv("THROUGHLINE_EXPORT_NO_MEMORY", "1")

    opts = em.options_from(em.build_parser().parse_args([]))

    assert opts["out"] == "/tmp/vault"
    assert opts["project"] == "throughline"
    assert opts["since"] == "2026-01-01"
    assert opts["include_generated"] is True
    assert opts["redact"] is True
    assert opts["tool_output"] == 400
    assert opts["memory"] is False


def test_the_command_line_still_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv(em.DEST_ENV, "/tmp/vom-env")
    monkeypatch.setenv("THROUGHLINE_EXPORT_PROJECT", "vom-env")
    argv = ["--out", "/tmp/explizit", "--project", "explizit", "--tool-output", "50"]
    opts = em.options_from(em.build_parser().parse_args(argv))
    assert opts["out"] == "/tmp/explizit"
    assert opts["project"] == "explizit"
    assert opts["tool_output"] == 50


def test_an_unset_environment_leaves_the_defaults_alone(monkeypatch):
    for key in (
        "THROUGHLINE_EXPORT_PROJECT",
        "THROUGHLINE_EXPORT_SINCE",
        "THROUGHLINE_EXPORT_INCLUDE_GENERATED",
        "THROUGHLINE_EXPORT_REDACT",
        "THROUGHLINE_EXPORT_TOOL_OUTPUT",
        "THROUGHLINE_EXPORT_NO_MEMORY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(em.DEST_ENV, "/tmp/vault")
    opts = em.options_from(em.build_parser().parse_args([]))
    assert opts["project"] is None
    assert opts["include_generated"] is False
    assert opts["redact"] is False
    assert opts["tool_output"] == 0
    assert opts["memory"] is True


def test_a_nonsense_tool_output_in_the_environment_is_ignored(monkeypatch):
    monkeypatch.setenv(em.DEST_ENV, "/tmp/vault")
    monkeypatch.setenv("THROUGHLINE_EXPORT_TOOL_OUTPUT", "viele")
    assert em.options_from(em.build_parser().parse_args([]))["tool_output"] == 0


def test_the_export_job_is_registered_with_a_fixed_command_line():
    from throughline.api.jobs import JOBS

    spec = JOBS["export-markdown"]
    assert spec.args[-1] == "export-markdown"
    # No destination anywhere in argv — it arrives through the environment.
    assert not any(arg.startswith("/") and "export" in arg for arg in spec.args[2:])
