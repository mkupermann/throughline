"""Hermes Agent (~/.hermes/sessions/) adapter."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Adapter, NormalisedConversation, NormalisedMessage

# Stable namespace so uuid5 derivation reproduces across machines.
_NS = uuid.UUID("c8d4f3b9-6e2a-4b7f-9a1e-3d8e2f6c5b4a")

_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool_result",
    "function": "tool_result",
}

_MSG_META_KEYS = (
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "finish_reason",
    "tool_call_id",
    "name",
)


def _parse_ts(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _normalise_content(content: Any) -> tuple[str, Any]:
    if isinstance(content, str):
        return content, None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    parts.append(str(block.get("text", "")))
                elif t == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '?')}]")
                elif t == "tool_result":
                    tc = block.get("content", "")
                    if isinstance(tc, str):
                        parts.append(tc[:500])
        return "\n".join(parts), content
    return ("" if content is None else str(content)[:2000]), None


class HermesAdapter(Adapter):
    name = "hermes"
    label = "Hermes Agent"
    home = Path("~/.hermes/sessions").expanduser()

    def discover(self) -> Iterable[Path]:
        if not self.home.exists():
            return []
        return sorted(self.home.glob("session_*.json"))

    def parse(self, path: Path) -> NormalisedConversation | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        raw_session_id = data.get("session_id") or path.stem
        messages_raw = data.get("messages") or []
        if not isinstance(messages_raw, list) or not messages_raw:
            return None
        started = _parse_ts(data.get("session_start"))
        ended = _parse_ts(data.get("last_updated"))
        model = data.get("model")
        platform = data.get("platform") or "hermes"

        norm_msgs: list[NormalisedMessage] = []
        for idx, msg in enumerate(messages_raw):
            if not isinstance(msg, dict):
                continue
            role = _ROLE_MAP.get((msg.get("role") or "").lower())
            if role is None:
                continue
            text, blocks = _normalise_content(msg.get("content"))
            meta = {k: msg[k] for k in _MSG_META_KEYS if k in msg and msg[k] is not None}
            norm_msgs.append(
                NormalisedMessage(
                    role=role,
                    content=text,
                    content_blocks=blocks,
                    created_at=started,
                    model=model if role == "assistant" else None,
                    uuid=str(uuid.uuid5(_NS, f"hermes:{raw_session_id}:msg:{idx}")),
                    metadata=meta,
                )
            )

        return NormalisedConversation(
            session_id=str(uuid.uuid5(_NS, f"hermes:{raw_session_id}")),
            project_path="hermes",
            model=model,
            entrypoint=platform,
            started_at=started,
            ended_at=ended,
            messages=norm_msgs,
            metadata={
                "source": "hermes",
                "hermes_session_id": raw_session_id,
                "base_url": data.get("base_url"),
                "system_prompt_chars": len(data.get("system_prompt") or ""),
                "tool_count": len(data.get("tools") or []),
            },
        )
