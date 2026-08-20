"""Which model answers, and whether the question leaves the machine.

Throughline's claim is that it is vendor-neutral and stays local unless told
otherwise. The first version of `ask` shelled out to one vendor's CLI, so the
headline feature both required that vendor's tool and sent excerpts of the
corpus to that vendor's API — inside a product whose pitch is the opposite.

These tests pin the selection rules that fix it. None of them make a network
call: the probes are stubbed, because what is under test is the decision, not
any particular server.
"""

from __future__ import annotations

import pytest

from throughline import llm


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "THROUGHLINE_ANSWER_BACKEND",
        "THROUGHLINE_ANSWER_MODEL",
        "THROUGHLINE_ANSWER_BASE_URL",
        "OPENAI_API_KEY",
        "CLAUDE_BIN",
    ):
        monkeypatch.delenv(var, raising=False)


def stub(monkeypatch, *, ollama: list[str] | None = None, claude: bool = False):
    """Pretend Ollama has *ollama* models, and that a `claude` CLI is or is not
    on PATH.

    The module stopped importing shutil when the claude CLI stopped being a
    backend, so the flag is applied to the stdlib itself. That keeps the tests
    honest: a claude binary really is discoverable, and must still never be
    used.
    """
    import shutil as _shutil

    monkeypatch.setattr(llm, "_ollama_chat_models", lambda: ollama or [])
    monkeypatch.setattr(_shutil, "which", lambda _n: "/usr/bin/claude" if claude else None)


# ── auto prefers what runs here ─────────────────────────────────────────────


def test_a_local_model_wins_over_every_remote_one(monkeypatch):
    """The whole point. With a local model running, a question must not reach
    the network — and the user must not have had to configure that."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-something")
    stub(monkeypatch, ollama=["qwen2.5:7b"], claude=True)

    info = llm.backend_info()
    assert info.backend == "ollama"
    assert info.local is True
    assert info.model == "qwen2.5:7b"


def test_an_openai_compatible_server_on_loopback_counts_as_local(monkeypatch):
    """LM Studio, llama.cpp, vLLM — an OpenAI-compatible endpoint on this
    machine is local, and saying otherwise would push people off it."""
    monkeypatch.setenv("THROUGHLINE_ANSWER_BASE_URL", "http://localhost:1234/v1")
    stub(monkeypatch, claude=True)

    info = llm.backend_info()
    assert info.backend == "openai"
    assert info.local is True


def test_a_remote_openai_compatible_server_is_not_called_local(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_BASE_URL", "https://llm.example.com/v1")
    stub(monkeypatch)
    assert llm.backend_info().local is False


def test_a_vendor_cli_on_path_is_not_a_backend(monkeypatch):
    """A `claude` binary is on most developers' PATH. It used to be enough to
    make `auto` send transcripts to one vendor on a machine whose owner had
    configured nothing. Presence is not consent."""
    stub(monkeypatch, claude=True)
    info = llm.backend_info()
    assert info.available is False
    assert info.backend == ""


def test_nothing_available_explains_both_ways_out(monkeypatch):
    stub(monkeypatch)
    info = llm.backend_info()
    assert info.available is False
    for hint in ("ollama", "BASE_URL"):
        assert hint.lower() in info.detail.lower()


# ── An embedding model cannot answer ────────────────────────────────────────


@pytest.mark.parametrize("name", ["nomic-embed-text:latest", "bge-m3", "gte-large", "e5-base"])
def test_embedding_models_are_filtered_out_of_the_candidate_list(monkeypatch, name):
    """Tests the filter itself, against Ollama's real response shape.

    The setup this product asks for pulls exactly one embedding model and
    nothing else, so this is the common case rather than an edge one — and an
    embedding model chosen to answer fails on the first question with an
    opaque server error instead of an honest fallback.
    """

    class FakeResponse:
        status = 200

        def read(self):
            return ('{"models": [{"name": "' + name + '"}, {"name": "qwen2.5:7b"}]}').encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert llm._ollama_chat_models() == ["qwen2.5:7b"]


def test_an_embedding_only_machine_reports_that_it_cannot_generate(monkeypatch):
    """An embedding model cannot answer, and there is no vendor CLI to fall
    through to any more — so say so instead of appearing ready."""
    stub(monkeypatch, ollama=[], claude=True)
    info = llm.backend_info()
    assert info.available is False
    assert "ollama" in info.detail.lower()


def test_the_pulled_model_is_used_rather_than_a_hardcoded_guess(monkeypatch):
    """An earlier version named `llama3.1:8b` unconditionally and reported
    Ollama as ready on a machine that did not have it."""
    stub(monkeypatch, ollama=["mistral-small:latest", "qwen2.5:7b"])
    assert llm.backend_info().model == "mistral-small:latest"


# ── Explicit choices are obeyed ─────────────────────────────────────────────


def test_naming_a_backend_overrides_the_probe_order(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-something")
    stub(monkeypatch, ollama=["qwen3.5:9b"])
    assert llm.backend_info().backend == "openai"


def test_naming_a_model_overrides_what_is_pulled(monkeypatch):
    """A model may be mid-download; refusing it would be second-guessing the
    person who typed its name."""
    monkeypatch.setenv("THROUGHLINE_ANSWER_MODEL", "llama3.3:70b")
    stub(monkeypatch, ollama=["qwen2.5:7b"])
    assert llm.backend_info().model == "llama3.3:70b"


def test_choosing_ollama_without_a_chat_model_says_what_to_do(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_BACKEND", "ollama")
    stub(monkeypatch, ollama=[])
    info = llm.backend_info()
    assert info.available is False
    assert "ollama pull" in info.detail


def test_choosing_openai_without_a_key_or_a_url_says_so(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_BACKEND", "openai")
    stub(monkeypatch)
    info = llm.backend_info()
    assert info.available is False
    assert "OPENAI_API_KEY" in info.detail


# ── Failure never raises into a question ────────────────────────────────────


def test_complete_returns_an_error_rather_than_raising(monkeypatch):
    """`ask` degrades to "here are the records I found", which is still more
    than the user had. An exception here would lose that."""
    stub(monkeypatch)
    text, err = llm.complete("anything")
    assert text is None
    assert err


def test_a_per_call_model_reaches_the_backend(monkeypatch):
    """`throughline ask --model X` accepted X and then ignored it.

    The flag was parsed, threaded through `ask.answer` into `_call_model`, and
    dropped on the floor: `complete()` had no parameter to receive it. A flag
    that silently does nothing is worse than a missing one — the user believes
    they chose.
    """
    stub(monkeypatch, ollama=["qwen2.5:7b"])
    seen: dict = {}

    def fake_post(url, payload, *, timeout, headers=None):
        seen.update(payload)
        return {"response": "ok"}

    monkeypatch.setattr(llm, "_http_json", fake_post)
    text, err = llm.complete("q", model="mistral-small:latest")
    assert (text, err) == ("ok", None)
    assert seen["model"] == "mistral-small:latest"


def test_without_a_per_call_model_the_probed_one_is_used(monkeypatch):
    stub(monkeypatch, ollama=["qwen2.5:7b"])
    seen: dict = {}
    monkeypatch.setattr(
        llm,
        "_http_json",
        lambda url, payload, *, timeout, headers=None: (seen.update(payload), {"response": "ok"})[1],
    )
    llm.complete("q")
    assert seen["model"] == "qwen2.5:7b"


# --------------------------------------------------------------------------- #
# The claude CLI is no longer a generation backend                            #
# --------------------------------------------------------------------------- #


def test_auto_never_falls_back_to_the_claude_cli(monkeypatch):
    # Shelling out to one vendor's CLI is the thing this module exists to
    # avoid. With nothing local available it must say so, not quietly reach
    # for a tool that carries the user's Anthropic credentials.
    stub(monkeypatch, ollama=[], claude=True)
    info = llm.backend_info()
    assert info.backend != "claude"
    assert not info.available


def test_asking_for_claude_explicitly_is_refused_with_a_reason(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_BACKEND", "claude")
    stub(monkeypatch, ollama=["qwen3.5:9b"], claude=True)
    info = llm.backend_info()
    assert not info.available
    assert "no longer" in info.detail.lower() or "not a backend" in info.detail.lower()


def test_the_unavailable_message_does_not_advertise_the_claude_cli(monkeypatch):
    stub(monkeypatch, ollama=[], claude=False)
    detail = llm.backend_info().detail
    assert "claude" not in detail.lower()
    assert "ollama" in detail.lower()


def test_claude_is_not_offered_as_a_default_model(monkeypatch):
    assert "claude" not in llm._DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# Schema-enforced output                                                      #
# --------------------------------------------------------------------------- #


def test_a_schema_is_passed_to_ollama_so_malformed_json_is_impossible(monkeypatch):
    # Ollama constrains generation to the schema, so a small model cannot emit
    # a trailing comma or an array of strings where objects were asked for.
    # That is what made local extraction unusable, not the model's size.
    sent = {}
    monkeypatch.setattr(
        llm, "_http_json", lambda url, body, **kw: sent.update(url=url, body=body) or {"response": "{}"}
    )
    stub(monkeypatch, ollama=["qwen3.5:9b"])

    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    text, error = llm.complete("gib ein Objekt", schema=schema)

    assert error is None
    assert sent["body"]["format"] == schema


def test_without_a_schema_nothing_extra_is_sent(monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "_http_json", lambda url, body, **kw: sent.update(body=body) or {"response": "ok"})
    stub(monkeypatch, ollama=["qwen3.5:9b"])

    llm.complete("hallo")

    assert "format" not in sent["body"]


def test_an_openai_backend_asks_for_a_json_object_when_given_a_schema(monkeypatch):
    sent = {}
    monkeypatch.setenv("THROUGHLINE_ANSWER_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr(
        llm,
        "_http_json",
        lambda url, body, **kw: sent.update(body=body) or {"choices": [{"message": {"content": "{}"}}]},
    )
    stub(monkeypatch, ollama=[])

    llm.complete("gib ein Objekt", schema={"type": "object"})

    assert sent["body"]["response_format"]["type"] == "json_object"


def test_a_schema_turns_off_thinking(monkeypatch):
    # A thinking model given a schema puts the constrained JSON in `thinking`
    # and leaves `response` empty, so the caller sees nothing at all. On a real
    # corpus that turned a working extraction into twenty empty results.
    sent = {}
    monkeypatch.setattr(llm, "_http_json", lambda url, body, **kw: sent.update(body=body) or {"response": "[]"})
    stub(monkeypatch, ollama=["qwen3.5:9b"])

    llm.complete("x", schema={"type": "array"})

    assert sent["body"]["think"] is False


def test_thinking_is_left_alone_without_a_schema(monkeypatch):
    # Unconstrained, a thinking model's reasoning is its own business and
    # switching it off would change answer quality for everyone.
    sent = {}
    monkeypatch.setattr(llm, "_http_json", lambda url, body, **kw: sent.update(body=body) or {"response": "ok"})
    stub(monkeypatch, ollama=["qwen3.5:9b"])

    llm.complete("x")

    assert "think" not in sent["body"]


def test_an_answer_that_arrived_in_the_thinking_field_is_still_returned(monkeypatch):
    # Belt and braces: a model that ignores think=false must not read as empty.
    monkeypatch.setattr(llm, "_http_json", lambda url, body, **kw: {"response": "", "thinking": '{"a": 1}'})
    stub(monkeypatch, ollama=["qwen3.5:9b"])

    text, error = llm.complete("x", schema={"type": "object"})

    assert error is None
    assert text == '{"a": 1}'


# --------------------------------------------------------------------------- #
# Which pulled model gets used                                                #
# --------------------------------------------------------------------------- #


def test_the_documented_default_wins_when_it_is_pulled(monkeypatch):
    # Ollama lists models by modification date, so taking the first one means
    # the next `ollama pull` of anything silently changes which model answers
    # — and the default named in the docs is never the one that runs.
    stub(monkeypatch, ollama=["llama3.2:1b", llm._DEFAULT_MODEL["ollama"], "mistral:7b"])
    assert llm.backend_info().model == llm._DEFAULT_MODEL["ollama"]


def test_without_the_default_the_first_pulled_model_is_used(monkeypatch):
    # Asking rather than assuming is still right: a machine that has never
    # pulled the default must use what it has, not report a model it lacks.
    stub(monkeypatch, ollama=["mistral:7b", "llama3.2:1b"])
    assert llm.backend_info().model == "mistral:7b"


def test_an_explicitly_named_model_still_beats_the_default(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_MODEL", "phi4:14b")
    stub(monkeypatch, ollama=[llm._DEFAULT_MODEL["ollama"]])
    assert llm.backend_info().model == "phi4:14b"


def test_a_tag_suffix_does_not_hide_the_default(monkeypatch):
    # Ollama reports "qwen3.5:9b" for `ollama pull qwen3.5:9b`, but a machine
    # may hold it as "qwen3.5:9b-instruct-q4_K_M".
    default = llm._DEFAULT_MODEL["ollama"]
    stub(monkeypatch, ollama=["mistral:7b", f"{default}-q4_K_M"])
    assert llm.backend_info().model == f"{default}-q4_K_M"
