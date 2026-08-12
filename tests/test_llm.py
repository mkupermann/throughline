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
    monkeypatch.setattr(llm, "_ollama_chat_models", lambda: ollama or [])
    monkeypatch.setattr(llm.shutil, "which", lambda _n: "/usr/bin/claude" if claude else None)


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


def test_falling_back_to_a_vendor_cli_is_reported_as_remote(monkeypatch):
    """Honest labelling: this path does send excerpts off the machine."""
    stub(monkeypatch, claude=True)
    info = llm.backend_info()
    assert info.backend == "claude"
    assert info.local is False


def test_nothing_available_explains_all_three_ways_out(monkeypatch):
    stub(monkeypatch)
    info = llm.backend_info()
    assert info.available is False
    for hint in ("ollama", "BASE_URL", "claude"):
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
            return (
                '{"models": [{"name": "' + name + '"}, {"name": "qwen2.5:7b"}]}'
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert llm._ollama_chat_models() == ["qwen2.5:7b"]


def test_an_embedding_only_machine_falls_through_to_the_next_backend(monkeypatch):
    stub(monkeypatch, ollama=[], claude=True)
    assert llm.backend_info().backend == "claude"


def test_the_pulled_model_is_used_rather_than_a_hardcoded_guess(monkeypatch):
    """An earlier version named `llama3.1:8b` unconditionally and reported
    Ollama as ready on a machine that did not have it."""
    stub(monkeypatch, ollama=["mistral-small:latest", "qwen2.5:7b"])
    assert llm.backend_info().model == "mistral-small:latest"


# ── Explicit choices are obeyed ─────────────────────────────────────────────


def test_naming_a_backend_overrides_the_probe_order(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_ANSWER_BACKEND", "claude")
    stub(monkeypatch, ollama=["qwen2.5:7b"], claude=True)
    assert llm.backend_info().backend == "claude"


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
        llm, "_http_json",
        lambda url, payload, *, timeout, headers=None: (seen.update(payload), {"response": "ok"})[1],
    )
    llm.complete("q")
    assert seen["model"] == "qwen2.5:7b"
