"""Text generation, from whichever model you point it at.

Throughline's claim is that it is vendor-neutral and that nothing leaves your
machine unless you say so. The first version of `ask` broke both halves of
that in one line: it shelled out to Anthropic's `claude` CLI, so the answer
feature required one specific vendor's tool and sent excerpts of the corpus to
one specific vendor's API — inside a product whose pitch is the opposite. The
mistake came from copying the extraction scripts' assumption rather than
questioning it.

So: a backend is chosen the same way the embedding backend already is —
probed, environment-driven, no new config format to learn — and the probe
order puts local models first, because that is what the product promises.

    THROUGHLINE_ANSWER_BACKEND   auto | ollama | openai | claude   (default: auto)
    THROUGHLINE_ANSWER_MODEL     model name for that backend
    THROUGHLINE_ANSWER_BASE_URL  OpenAI-compatible endpoint (LM Studio, vLLM,
                                 llama.cpp, LiteLLM, a colleague's server…)
    OPENAI_API_KEY               only read by the `openai` backend

`auto` tries Ollama, then any OpenAI-compatible server named by BASE_URL, then
the `claude` CLI, then hosted OpenAI. A machine with Ollama running never
reaches a network call, and never had to be configured to avoid one.

Only stdlib: this module must not add a dependency to answer a question, and
every backend here speaks plain HTTP or a subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

#: Where Ollama listens unless told otherwise.
_OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

#: LM Studio's default. Probed only when the user named it in BASE_URL — this
#: module does not scan ports looking for servers.
_DEFAULT_OPENAI_COMPAT = "http://localhost:1234/v1"

#: Sensible per-backend defaults. Small instruct models are the right size for
#: this job: read twelve short excerpts, answer, cite. A frontier model is not
#: what makes the answer good here — the retrieval is.
_DEFAULT_MODEL = {
    "ollama": "llama3.1:8b",
    "openai": "gpt-4o-mini",
    "claude": "haiku",
}


@dataclass
class LLMInfo:
    available: bool
    backend: str = ""
    model: str = ""
    detail: str = ""
    #: True when the prompt stays on this machine. Surfaced so the UI can say
    #: so plainly rather than leaving the user to infer it.
    local: bool = False

    def __str__(self) -> str:  # pragma: no cover - display only
        if not self.available:
            return f"unavailable ({self.detail})"
        where = "local" if self.local else "remote"
        return f"{self.backend}/{self.model} ({where})"


def _http_json(url: str, payload: dict, *, timeout: float, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


#: Substrings that mark an Ollama model as embedding-only. Such a model cannot
#: generate text, and picking one would fail at the first question with an
#: opaque error from the server rather than an honest "nothing here can answer".
#: A machine set up for Throughline's semantic search has exactly one of these
#: pulled and nothing else, which is the common case, not an edge case.
_EMBEDDING_ONLY = ("embed", "bge-", "gte-", "e5-")


def _ollama_chat_models() -> list[str]:
    """Generation-capable models Ollama actually has pulled.

    Asks rather than assumes. The first version of this defaulted to
    `llama3.1:8b` and reported Ollama as available on a machine whose only
    model was `nomic-embed-text` — a confident default that would have failed
    on the first real question.
    """
    try:
        with urllib.request.urlopen(f"{_OLLAMA_URL}/api/tags", timeout=1.5) as resp:
            if resp.status != 200:
                return []
            data = json.loads(resp.read().decode())
    except Exception:
        return []
    names = [str(m.get("name", "")) for m in data.get("models", [])]
    return [n for n in names if n and not any(mark in n.lower() for mark in _EMBEDDING_ONLY)]


def _openai_compat_base() -> str | None:
    """An OpenAI-compatible endpoint the user has named, or None.

    Only what BASE_URL says. Probing localhost:1234 uninvited would mean a
    question silently going to whatever happens to be listening there.
    """
    base = os.environ.get("THROUGHLINE_ANSWER_BASE_URL", "").strip()
    return base.rstrip("/") if base else None


def backend_info() -> LLMInfo:
    """Which model will answer, and whether it runs on this machine."""
    preferred = os.environ.get("THROUGHLINE_ANSWER_BACKEND", "auto").strip().lower() or "auto"
    model = os.environ.get("THROUGHLINE_ANSWER_MODEL", "").strip()
    base = _openai_compat_base()

    def pick(backend: str, *, local: bool, detail: str = "") -> LLMInfo:
        chosen = model
        if not chosen and backend == "ollama":
            # Whatever is actually pulled, in Ollama's own order. A hardcoded
            # name would name a model the machine may not have.
            pulled = _ollama_chat_models()
            chosen = pulled[0] if pulled else ""
        return LLMInfo(
            available=True,
            backend=backend,
            model=chosen or _DEFAULT_MODEL.get(backend, ""),
            local=local,
            detail=detail,
        )

    if preferred == "ollama":
        chat = _ollama_chat_models()
        if not chat:
            return LLMInfo(
                False,
                detail=(
                    f"Ollama at {_OLLAMA_URL} has no generation model pulled "
                    "(an embedding model cannot answer). Try: ollama pull llama3.1:8b"
                ),
            )
        # An explicitly named model wins even if it is not in the list — the
        # user may be pulling it right now, and second-guessing them here would
        # be worse than letting the call report the real error.
        return pick("ollama", local=True, detail=", ".join(chat[:3]))

    if preferred == "openai":
        if base:
            return pick("openai", local=_is_loopback(base), detail=base)
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return LLMInfo(False, detail="OPENAI_API_KEY is not set and no BASE_URL was given.")
        return pick("openai", local=False, detail="api.openai.com")

    if preferred == "claude":
        if not shutil.which(os.environ.get("CLAUDE_BIN", "claude")):
            return LLMInfo(False, detail="No `claude` CLI on PATH.")
        return pick("claude", local=False, detail="Anthropic API via the claude CLI")

    # auto — local first, and never silently reaching the network when
    # something on this machine can do the job.
    chat = _ollama_chat_models()
    if chat:
        return pick("ollama", local=True, detail=", ".join(chat[:3]))
    if base:
        return pick("openai", local=_is_loopback(base), detail=base)
    if shutil.which(os.environ.get("CLAUDE_BIN", "claude")):
        return pick("claude", local=False, detail="Anthropic API via the claude CLI")
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return pick("openai", local=False, detail="api.openai.com")
    return LLMInfo(
        False,
        detail=(
            "No model available. Start Ollama (`ollama serve`), set "
            "THROUGHLINE_ANSWER_BASE_URL to an OpenAI-compatible server, or "
            "install the `claude` CLI."
        ),
    )


def _is_loopback(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0"))


def complete(
    prompt: str,
    *,
    timeout: float = 180.0,
    cwd: str | None = None,
    model: str | None = None,
) -> tuple[str | None, str | None]:
    """Run *prompt* through the configured model. Returns (text, error).

    `model` overrides the resolved model for this one call — what
    `throughline ask --model` is for — while the backend stays whatever the
    probe found. The backend is a property of the machine; the model is a
    per-question choice.

    Never raises: a question that cannot be answered has to degrade to "here
    are the records I found", which is still more than the user had before.
    """
    info = backend_info()
    if not info.available:
        return None, info.detail
    if model:
        info = replace(info, model=model)

    try:
        if info.backend == "ollama":
            data = _http_json(
                f"{_OLLAMA_URL}/api/generate",
                {"model": info.model, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            return (data.get("response") or "").strip(), None

        if info.backend == "openai":
            base = _openai_compat_base() or "https://api.openai.com/v1"
            headers = {}
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            # A local server usually wants no key; a hosted one always does.
            if key:
                headers["authorization"] = f"Bearer {key}"
            data = _http_json(
                f"{base}/chat/completions",
                {
                    "model": info.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=timeout,
                headers=headers,
            )
            return (data["choices"][0]["message"]["content"] or "").strip(), None

        # claude CLI
        cli = shutil.which(os.environ.get("CLAUDE_BIN", "claude"))
        proc = subprocess.run(
            [cli, "-p", prompt, "--model", info.model],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        if proc.returncode != 0:
            return None, f"claude exited {proc.returncode}"
        return proc.stdout.strip(), None

    except subprocess.TimeoutExpired:
        return None, f"{info.backend} timed out after {timeout:.0f}s"
    except urllib.error.HTTPError as e:
        return None, f"{info.backend} returned HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"{info.backend} unreachable: {e.reason}"
    except (KeyError, IndexError, json.JSONDecodeError):
        return None, f"{info.backend} returned an unexpected response shape"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
