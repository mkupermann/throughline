"""CLI behavior checks for the packaged semantic-search job."""

from types import SimpleNamespace

import pytest

from throughline.jobs import search_semantic


def test_empty_embedding_store_prints_an_installed_cli_remediation(monkeypatch, capsys):
    """Zero embeddings should exit cleanly with a command available from the wheel."""

    class Cursor:
        def execute(self, *_args, **_kwargs):
            pass

        def fetchone(self):
            return {"n": 0}

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

    backend = SimpleNamespace(model="test-model", name="ollama")
    monkeypatch.setattr(search_semantic, "pick_backend", lambda _name: backend)
    monkeypatch.setattr(search_semantic, "_connect", Connection)
    monkeypatch.setattr("sys.argv", ["search-semantic", "query"])

    with pytest.raises(SystemExit) as exc:
        search_semantic.main()

    assert exc.value.code == 3
    assert "throughline embed --backend ollama" in capsys.readouterr().err
