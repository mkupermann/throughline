"""Embedding backend-selection contracts."""

from throughline.jobs import generate_embeddings


def test_embedding_auto_prefers_openai_when_a_key_is_present(monkeypatch):
    """This precedence is a data-egress boundary and must remain documented."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    backend = generate_embeddings.pick_backend("auto")

    assert isinstance(backend, generate_embeddings.OpenAIBackend)
