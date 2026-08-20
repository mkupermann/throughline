"""Entity extraction must go through the shared model backend.

Every other generating job calls `throughline.llm.complete`, which probes for
a local model first and reports honestly whether the call leaves the machine.
This one shelled out to the `claude` CLI directly, so it ignored the user's
configured backend, could not run without one specific vendor's tool, and sent
transcripts to that vendor without the backend probe ever saying so.
"""

from __future__ import annotations

from throughline.jobs import extract_entities as ee


def test_the_job_does_not_shell_out_to_a_vendor_cli():
    source = __import__("pathlib").Path(ee.__file__).read_text(encoding="utf-8")
    assert "CLAUDE_BIN" not in source
    assert "-p", "prompt" not in source
    assert "call_claude" not in source


def test_generation_goes_through_the_shared_backend(monkeypatch):
    seen = {}

    def fake_complete(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return '{"entities": [], "relationships": []}', None

    monkeypatch.setattr(ee.llm, "complete", fake_complete)
    assert ee.call_model("finde Entitäten") == '{"entities": [], "relationships": []}'
    assert seen["prompt"] == "finde Entitäten"


def test_a_backend_error_is_reported_and_yields_nothing(monkeypatch, capsys):
    monkeypatch.setattr(ee.llm, "complete", lambda prompt, **kw: (None, "ollama timed out after 300s"))
    assert ee.call_model("x") == ""
    assert "ollama timed out" in capsys.readouterr().out


def test_the_call_still_runs_outside_the_users_project_directory(monkeypatch):
    # Whatever the backend, a model call made from the repo's own working
    # directory gets filed as the user's work by the next ingest.
    seen = {}
    monkeypatch.setattr(ee.llm, "complete", lambda prompt, **kw: (seen.update(kw), ("{}", None))[1])
    ee.call_model("x")
    assert str(ee.agent_call_cwd()) == seen["cwd"]
