"""Embedding backend-selection contracts."""

from throughline.jobs import generate_embeddings


def test_embedding_auto_prefers_openai_when_a_key_is_present(monkeypatch):
    """This precedence is a data-egress boundary and must remain documented."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    backend = generate_embeddings.pick_backend("auto")

    assert isinstance(backend, generate_embeddings.OpenAIBackend)


# --------------------------------------------------------------------------- #
# Where Ollama lives                                                          #
# --------------------------------------------------------------------------- #


def test_the_embedding_job_honours_ollama_host(monkeypatch):
    """`llm.py` reads OLLAMA_HOST; this module hardcoded localhost.

    Two modules, two behaviours, and one of them not configurable at all: in a
    container — or anywhere Ollama is not on this loopback — embeddings could
    never work no matter what was configured, and the failure said "Ollama is
    not running on http://localhost:11434", which was true and useless.
    """
    import importlib

    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")
    ge = importlib.reload(importlib.import_module("throughline.jobs.generate_embeddings"))
    try:
        assert ge.OLLAMA_URL == "http://ollama:11434"
    finally:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        importlib.reload(ge)


def test_a_trailing_slash_does_not_produce_a_double_slash(monkeypatch):
    import importlib

    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434/")
    ge = importlib.reload(importlib.import_module("throughline.jobs.generate_embeddings"))
    try:
        assert ge.OLLAMA_URL == "http://ollama:11434"
    finally:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        importlib.reload(ge)


def test_without_the_variable_it_still_defaults_to_this_machine(monkeypatch):
    import importlib

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    ge = importlib.reload(importlib.import_module("throughline.jobs.generate_embeddings"))
    assert ge.OLLAMA_URL == "http://localhost:11434"
