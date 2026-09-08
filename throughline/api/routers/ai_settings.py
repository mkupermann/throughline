"""Purpose-specific AI settings; credentials stay in the existing provider store."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from throughline import ai_runtime, embedding
from throughline.queries._exec import one, rows

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["AI settings"])


class PurposeBinding(BaseModel):
    provider_id: int | None = None
    cli: Literal["codex", "vibe", "claude"] | None = None
    model: str = Field(default="", max_length=200)
    embedding_dim: Literal[768, 1536] | None = None


@router.get("/ai/settings")
def settings_view(settings: Settings = Depends(get_settings)):
    with connection(settings) as conn:
        bindings = rows(conn, "SELECT * FROM ai_purposes ORDER BY purpose")
    try:
        bridge = ai_runtime.bridge("/capabilities")
    except Exception:
        bridge = {"clis": {}, "error": "Host CLI bridge is unavailable. Configure or start it on the host."}
    return {"purposes": ai_runtime.PURPOSES, "bindings": bindings, "bridge": bridge}


@router.put("/ai/settings/{purpose}")
def save(purpose: str, body: PurposeBinding, settings: Settings = Depends(get_settings)):
    from ..jobs import runner

    if runner.current("process-all"):
        raise HTTPException(409, "Stop the complete pass before changing its AI destinations.")
    if purpose not in ai_runtime.PURPOSES or bool(body.provider_id) == bool(body.cli):
        raise HTTPException(422, "Select one API provider or one installed CLI for a supported purpose.")
    if purpose == "embeddings" and (body.cli or not body.embedding_dim):
        raise HTTPException(
            422,
            "Embeddings require an embedding API and a supported vector dimension; chat CLIs do not provide embeddings.",
        )
    if body.provider_id and not body.model.strip():
        raise HTTPException(422, "Select a model.")
    with connection(settings) as conn:
        if body.provider_id:
            p = one(conn, "SELECT provider_type, enabled FROM pm_ai_providers WHERE id=%s", (body.provider_id,))
            if not p or not p["enabled"]:
                raise HTTPException(422, "The selected provider does not exist or is disabled.")
            if purpose == "embeddings" and p["provider_type"] not in (
                "ollama",
                "openai",
                "openai_compatible",
                "mistral",
                "openrouter",
            ):
                raise HTTPException(
                    422,
                    "This provider has no supported embedding adapter. Use Ollama or an OpenAI-compatible embedding API.",
                )
        result = one(
            conn,
            """INSERT INTO ai_purposes(purpose,provider_id,cli,model,embedding_dim) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT(purpose) DO UPDATE SET provider_id=EXCLUDED.provider_id, cli=EXCLUDED.cli,
            model=EXCLUDED.model,embedding_dim=EXCLUDED.embedding_dim,updated_at=now() RETURNING *""",
            (
                purpose,
                body.provider_id,
                body.cli,
                body.model.strip(),
                body.embedding_dim if purpose == "embeddings" else None,
            ),
        )
        conn.commit()
    embedding.reset()
    return result


@router.post("/ai/settings/{purpose}/test")
def test_binding(purpose: str):
    if purpose not in ai_runtime.PURPOSES:
        raise HTTPException(404, "Unknown purpose")
    try:
        config = ai_runtime.route(purpose)
        if not config:
            raise ValueError("Save an explicit selection first")
        if purpose == "embeddings":
            backend = ai_runtime.embedding_backend(config)
            vector = backend.embed(["Throughline connection test"])[0]
            if len(vector) != backend.dim:
                raise ValueError("Embedding dimension mismatch")
        else:
            schema = {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            }
            output = ai_runtime.generate(
                config, 'Return JSON {"status":"OK"}. This is a synthetic connection test.', schema=schema, timeout=60
            )
            if json.loads(output).get("status") != "OK":
                raise ValueError("Structured connection test failed")
        return {"ok": True, "destination": ai_runtime.destination(config)}
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Connection test failed ({type(exc).__name__}). Check endpoint, model and login.",
        }
