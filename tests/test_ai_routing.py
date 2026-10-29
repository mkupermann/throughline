"""Purpose routing must not leak into a fallback or accept incomplete model output."""

import json
from types import SimpleNamespace

import pytest

from throughline import ai_runtime as ai
from throughline import llm
from throughline.jobs import generate_titles as titles
from throughline.jobs import process_all


def config(kind="ollama", **values):
    return {
        "provider_type": kind,
        "model": "test-model",
        "enabled": True,
        "base_url": "http://localhost:11434",
        "api_key": "test-secret",
        "cli": None,
        "embedding_dim": 768,
        **values,
    }


def test_explicit_failure_never_uses_legacy_backend(monkeypatch):
    monkeypatch.setattr(ai, "route", lambda purpose: config(enabled=False))
    monkeypatch.setattr(llm, "backend_info", lambda: pytest.fail("Unexpected fallback"))
    text, error = llm.complete("source", purpose="titles")
    assert text is None and "Selected AI failed" in error
    assert "test-secret" not in error


def test_array_schema_is_wrapped_and_unwrapped(monkeypatch):
    seen = {}

    def post(url, payload, **kwargs):
        seen.update(payload)
        return {"response": '{"items": [{"fact": "example"}]}', "done_reason": "stop"}

    monkeypatch.setattr(ai, "post", post)
    result = ai.generate(config(), "extract", {"type": "array", "items": {"type": "object"}})
    assert json.loads(result) == [{"fact": "example"}]
    assert seen["format"]["type"] == "object" and seen["think"] is False


@pytest.mark.parametrize("response", [{"response": "{}", "done_reason": "length"}, {"thinking": "{}"}])
def test_ollama_truncation_or_thinking_is_not_a_final_answer(monkeypatch, response):
    monkeypatch.setattr(ai, "post", lambda *a, **kw: response)
    with pytest.raises(RuntimeError):
        ai.generate(config(), "source", {"type": "object"})


def test_anthropic_uses_its_api_and_schema(monkeypatch):
    seen = {}

    def post(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers)
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"title":"Example title"}'}]}

    monkeypatch.setattr(ai, "post", post)
    ai.generate(config("anthropic", base_url="https://api.anthropic.com"), "source", {"type": "object"})
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["payload"]["output_config"]["format"]["type"] == "json_schema"
    assert seen["headers"]["x-api-key"] == "test-secret"


def test_embedding_dimension_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(ai, "post", lambda *a, **kw: {"embeddings": [[1, 2]]})
    with pytest.raises(RuntimeError):
        ai.embedding_backend(config()).embed(["source"])


def test_title_source_strips_envelopes_and_rejects_thinking(monkeypatch):
    preview = titles.build_preview(
        [
            ("user", "<environment_context>private setup</environment_context>\nBuild a garden planner"),
            ("assistant", "[Tool: ignored]"),
        ]
    )
    assert "garden planner" in preview and "private setup" not in preview and "Tool:" not in preview
    monkeypatch.setattr(titles._llm, "complete", lambda *a, **kw: ('{"title":"Thinking Process:"}', None))
    assert titles.call_model(preview) == ""
    monkeypatch.setattr(titles._llm, "complete", lambda *a, **kw: ('{"title":"Garden planning application"}', None))
    assert titles.call_model(preview) == "Garden planning application"


def test_full_pass_attempts_later_steps_after_failure(monkeypatch, capsys):
    names = ("titles", "extract", "embed")
    monkeypatch.setattr(process_all, "STEPS", names)
    monkeypatch.setattr(process_all, "check_requirement", lambda req: None)
    called = []

    def run(args, env, check):
        called.append(args)
        assert env["THROUGHLINE_TITLE_LIMIT"] == "0"
        return SimpleNamespace(returncode=1 if len(called) == 1 else 0)

    monkeypatch.setattr(process_all.subprocess, "run", run)
    with pytest.raises(SystemExit) as e:
        process_all.main()
    assert e.value.code == 1 and len(called) == 3
    assert process_all.FULL_LIMIT in called[1]
    assert "Incomplete pass" in capsys.readouterr().out


def test_reflection_in_full_pass_is_review_only():
    assert "--dry-run" in process_all.command("reflect")
    assert "--limit" in process_all.command("extract")


def test_embedding_identity_separates_endpoints_without_changing_api_model(monkeypatch):
    first = ai.embedding_backend(config(base_url="http://localhost:11434"))
    second = ai.embedding_backend(config(base_url="http://localhost:11435"))
    assert first.model != second.model
    seen = {}

    def post(url, payload):
        seen.update(payload)
        return {"embeddings": [[0.0] * first.dim]}

    monkeypatch.setattr(ai, "post", post)
    first.embed(["example"])
    assert seen["model"] == config()["model"]
