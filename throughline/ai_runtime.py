"""Explicit per-purpose AI routing. A selected backend never falls back."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import psycopg2

from throughline.config import get_db_config
from throughline.queries._exec import one
from throughline.queries.pm import _PROVIDER_DEFAULT_BASE

PURPOSES = ("answer", "titles", "project_names", "extraction", "reflection", "embeddings")


def route(purpose):
    if purpose not in PURPOSES:
        raise ValueError("Unknown AI purpose")
    conn = psycopg2.connect(**{**get_db_config(), "connect_timeout": 2})
    try:
        return one(
            conn,
            """SELECT a.*, p.provider_type, p.base_url, p.api_key, p.enabled, p.name
            FROM ai_purposes a LEFT JOIN pm_ai_providers p ON p.id=a.provider_id
            WHERE purpose=%s""",
            (purpose,),
        )
    except psycopg2.errors.UndefinedTable:
        return None  # Compatibility with installations before migration 012.
    finally:
        conn.close()


def post(url, payload, headers=None, timeout=180):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def bridge(path, payload=None, timeout=180):
    base = os.environ.get("THROUGHLINE_CLI_BRIDGE_URL", "").rstrip("/")
    token = os.environ.get("THROUGHLINE_CLI_BRIDGE_TOKEN", "")
    if not base or not token:
        raise RuntimeError("Host CLI bridge is not configured")
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        return post(base + path, payload, headers, timeout)
    req = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def destination(config):
    if config.get("cli"):
        return f"Host CLI: {config['cli']} (uses its configured service and login)"
    return config.get("base_url") or _PROVIDER_DEFAULT_BASE.get(config["provider_type"], "")


def generate(config, prompt, schema=None, timeout=180):
    unwrap = bool(schema and schema.get("type") == "array")
    if unwrap:
        schema = {
            "type": "object",
            "properties": {"items": schema},
            "required": ["items"],
            "additionalProperties": False,
        }
        prompt += '\nReturn the array inside the JSON object field "items".'
    if config.get("cli"):
        result = bridge(
            "/complete",
            {"cli": config["cli"], "model": config["model"], "prompt": prompt, "schema": schema, "timeout": timeout},
            timeout + 10,
        )
        if result.get("error"):
            raise RuntimeError(result["error"])
        return json.dumps(json.loads(result["text"])["items"]) if unwrap else result["text"]
    if not config.get("enabled"):
        raise RuntimeError("Selected AI provider is disabled")
    kind, model = config["provider_type"], config["model"]
    base = destination(config).rstrip("/")
    key = config.get("api_key") or ""
    if not model:
        raise RuntimeError("Select a model for this purpose")
    if kind == "ollama":
        payload = {"model": model, "prompt": prompt, "stream": False}
        if schema:
            payload.update(format=schema, think=False)
        data = post(base + "/api/generate", payload, timeout=timeout)
        if data.get("done_reason") == "length":
            raise RuntimeError("Model output was truncated")
        text = data.get("response")
    elif kind == "anthropic":
        instruction = prompt + ("\nReturn only JSON matching: " + json.dumps(schema) if schema else "")
        payload = {"model": model, "max_tokens": 8192, "messages": [{"role": "user", "content": instruction}]}
        if schema:
            payload["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        data = post(
            base + "/v1/messages",
            payload,
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout,
        )
        if data.get("stop_reason") != "end_turn":
            raise RuntimeError("Model output was truncated")
        text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
    elif kind == "google":
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        if schema:
            payload["generationConfig"] = {"responseMimeType": "application/json", "responseJsonSchema": schema}
        data = post(
            base + "/v1beta/models/" + urllib.parse.quote(model.removeprefix("models/"), safe="") + ":generateContent",
            payload,
            {"x-goog-api-key": key},
            timeout,
        )
        candidate = data["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            raise RuntimeError("Model did not finish its answer")
        text = "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
    else:
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            }
        data = post(base + "/chat/completions", payload, {"Authorization": f"Bearer {key}"} if key else {}, timeout)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise RuntimeError("Model did not finish its answer")
        text = choice["message"].get("content")
    if not text or not isinstance(text, str):
        raise RuntimeError("No final answer returned")
    return json.dumps(json.loads(text)["items"]) if unwrap else text


def info(purpose):
    from throughline.llm import LLMInfo

    config = route(purpose)
    if config is None:
        return None
    if config.get("cli"):
        available = bridge("/capabilities")["clis"].get(config["cli"], {}).get("installed", False)
        return LLMInfo(available, config["cli"], config["model"] or "CLI default", destination(config), False)
    return LLMInfo(
        bool(config.get("enabled")),
        config["provider_type"],
        config["model"],
        destination(config),
        config["provider_type"] == "ollama"
        and urllib.parse.urlsplit(destination(config)).hostname
        in ("localhost", "127.0.0.1", "host.docker.internal", "::1"),
    )


def embedding_backend(config):
    from throughline.jobs.generate_embeddings import Backend

    if config.get("cli") or not config.get("enabled"):
        raise RuntimeError("Selected embedding provider is unavailable")

    class ConfiguredEmbedding(Backend):
        name = config["provider_type"]
        # Namespace vectors by destination as well as model; equal names need not mean equal spaces.
        import hashlib

        model = (
            config["model"]
            + "@"
            + hashlib.sha256((config["provider_type"] + ":" + destination(config)).encode()).hexdigest()[:16]
        )
        dim = config["embedding_dim"]
        column = f"embedding_{dim}"
        batch = 16
        max_chars = 2000

        def embed(self, texts):
            base = destination(config).rstrip("/")
            if self.name == "ollama":
                data = post(base + "/api/embed", {"model": config["model"], "input": list(texts)})
                vectors = data["embeddings"]
            else:
                data = post(
                    base + "/embeddings",
                    {"model": config["model"], "input": list(texts)},
                    {"Authorization": f"Bearer {config.get('api_key') or ''}"},
                )
                vectors = [item["embedding"] for item in sorted(data["data"], key=lambda item: item["index"])]
            if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
                raise RuntimeError("Embedding response count or dimension mismatch")
            return vectors

    return ConfiguredEmbedding()
