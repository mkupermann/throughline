"""Embedding-backend resolution, shared by every surface.

``scripts/generate_embeddings.py`` owns the actual backends (Ollama, OpenAI).
This module is the thin, *fail-soft* front door to them: it never raises, never
triggers a model pull, and never blocks a web request on a network call it
cannot bound.

That matters because the API embeds a user's query on the request path. A
missing backend must degrade the search to lexical-only and say so — not hang,
and not 500.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Sequence

from throughline.config import repo_root

_lock = threading.Lock()
_resolved = False
_backend: Any | None = None
_reason: str = ""


def _load_module():
    """Import ``scripts/generate_embeddings.py`` without polluting sys.path
    permanently more than once."""
    scripts = str(repo_root() / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import generate_embeddings  # type: ignore

    return generate_embeddings


@dataclass(frozen=True)
class BackendInfo:
    """What the caller needs to run a vector query, or why it cannot."""

    available: bool
    name: str | None = None
    model: str | None = None
    column: str | None = None
    dim: int | None = None
    reason: str = ""

    @property
    def label(self) -> str:
        if not self.available:
            return "unavailable"
        return f"{self.name}/{self.model} ({self.dim}d)"


def _probe(preferred: str) -> str:
    """Cheap reachability check. Returns '' when usable, else a reason.

    Deliberately does not call ``pick_backend`` — that can pull a model, which
    is a multi-minute operation and must never happen inside a request.
    """
    ge = _load_module()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if preferred == "openai":
        return "" if key else "OPENAI_API_KEY is not set."
    if preferred == "auto" and key:
        return ""
    if not ge.ollama_up():
        return (
            "Ollama is not running on http://localhost:11434 and OPENAI_API_KEY "
            "is not set. Start Ollama (`ollama serve`) or set an API key."
        )
    if not ge.ollama_has_model(ge.OLLAMA_MODEL):
        return f"Ollama model `{ge.OLLAMA_MODEL}` is not pulled. Run: ollama pull {ge.OLLAMA_MODEL}"
    return ""


def get_backend(preferred: str = "auto", refresh: bool = False):
    """Return a backend object, or None. Never raises."""
    global _resolved, _backend, _reason
    with _lock:
        if _resolved and not refresh:
            return _backend
        _resolved = True
        _backend = None
        try:
            reason = _probe(preferred)
            if reason:
                _reason = reason
                return None
            ge = _load_module()
            _backend = ge.pick_backend(preferred)
            _reason = ""
        except SystemExit:
            # pick_backend calls sys.exit on failure; a CLI habit that must
            # not take down a web server.
            _reason = "Backend initialisation aborted."
            _backend = None
        except Exception as exc:
            _reason = f"Backend unavailable: {exc}"
            _backend = None
        return _backend


def backend_info(preferred: str = "auto") -> BackendInfo:
    b = get_backend(preferred)
    if b is None:
        return BackendInfo(available=False, reason=_reason or "No embedding backend configured.")
    return BackendInfo(
        available=True, name=b.name, model=b.model, column=b.column, dim=b.dim
    )


def embed_query(text: str, preferred: str = "auto") -> list[float] | None:
    """Embed one short string, or None if that is not possible."""
    b = get_backend(preferred)
    if b is None or not text:
        return None
    try:
        vec = b.embed([text[: b.max_chars]])[0]
        return vec if len(vec) == b.dim else None
    except Exception:
        return None


def vec_literal(vec: Sequence[float]) -> str:
    from throughline.queries.semantic import vec_literal as _v

    return _v(vec)


def reset() -> None:
    """Drop the cached backend. For tests, and for an /operate 'retry' action."""
    global _resolved, _backend, _reason
    with _lock:
        _resolved = False
        _backend = None
        _reason = ""
