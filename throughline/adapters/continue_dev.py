"""Continue.dev (~/.continue/sessions/) adapter.

Continue stores sessions in a couple of historical shapes:

1. ``~/.continue/sessions/sessions.json`` — JSON *array* of session
   objects (older layout).
2. ``~/.continue/sessions/<session_id>.json`` — one file per session
   (newer layout).

This adapter handles both. Each session is expected to have ``history``
(or ``messages``) — a list of turns ``{role, content, ...}``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Adapter, NormalisedConversation, NormalisedMessage

_NS = uuid.UUID("5b3f2e8d-7a4c-4d9b-9c1e-8f7a6d5c4b3e")
_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool_result",
    "function": "tool_result",
}


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Continue uses ms-since-epoch in some versions.
        if value > 1_000_000_000_000:
            value = value / 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _stringify(content: Any) -> tuple[str, Any]:
    """Continue messages can be strings or arrays of content parts."""
    if content is None:
        return "", None
    if isinstance(content, str):
        return content, None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif "content" in block and isinstance(block["content"], str):
                    parts.append(block["content"])
            else:
                parts.append(str(block))
        return "\n".join(parts), content
    return json.dumps(content, ensure_ascii=False)[:2000], None


def _session_from_dict(d: dict[str, Any], source_path: Path) -> NormalisedConversation | None:
    raw_session_id = d.get("sessionId") or d.get("id") or d.get("title") or source_path.stem
    history = d.get("history") or d.get("messages") or []
    if not isinstance(history, list) or not history:
        return None

    started = (
        _parse_ts(d.get("startTime"))
        or _parse_ts(d.get("createdAt"))
        or _parse_ts(d.get("session_start"))
        or _parse_ts(d.get("timestamp"))
        or datetime.now(timezone.utc)
    )
    ended = _parse_ts(d.get("endTime")) or _parse_ts(d.get("updatedAt")) or _parse_ts(d.get("last_updated")) or started

    model = (
        d.get("model")
        or (d.get("config") or {}).get("model")
        or (history[0].get("model") if isinstance(history[0], dict) else None)
    )

    norm: list[NormalisedMessage] = []
    for idx, turn in enumerate(history):
        if not isinstance(turn, dict):
            continue
        # Continue's "history" can wrap the actual message under "message".
        msg = turn.get("message") if isinstance(turn.get("message"), dict) else turn
        role = _ROLE_MAP.get((msg.get("role") or "").lower())
        if role is None:
            continue
        text, blocks = _stringify(msg.get("content"))
        norm.append(
            NormalisedMessage(
                role=role,
                content=text,
                content_blocks=blocks,
                created_at=_parse_ts(turn.get("timestamp")) or started,
                model=msg.get("model") or (model if role == "assistant" else None),
                uuid=str(uuid.uuid5(_NS, f"continue:{raw_session_id}:msg:{idx}")),
                metadata={
                    k: v
                    for k, v in turn.items()
                    if k not in ("message", "role", "content", "timestamp") and v is not None
                },
            )
        )

    if not norm:
        return None

    return NormalisedConversation(
        session_id=str(uuid.uuid5(_NS, f"continue:{raw_session_id}")),
        project_path="continue",
        model=model,
        entrypoint="continue.dev",
        source_tool="continue",
        started_at=started,
        ended_at=ended,
        messages=norm,
        summary=d.get("title") or None,
        metadata={
            "source": "continue",
            "continue_session_id": str(raw_session_id),
            "source_file": source_path.name,
        },
    )


class ContinueDevAdapter(Adapter):
    name = "continue"
    label = "Continue.dev"
    home = Path("~/.continue/sessions").expanduser()

    def discover(self) -> Iterable[Path]:
        if not self.home.exists():
            return []
        files: list[Path] = []
        # Per-session files (newer layout).
        files.extend(p for p in self.home.glob("*.json") if p.name != "sessions.json")
        # Aggregate sessions.json (older layout) — still indexed so we
        # can detect content changes and reparse all of it.
        agg = self.home / "sessions.json"
        if agg.exists():
            files.append(agg)
        return sorted(set(files))

    def parse(self, path: Path) -> NormalisedConversation | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        # sessions.json is an array. We can't represent N conversations
        # via a single NormalisedConversation, so fold them into one
        # "Continue (combined)" conversation when the file is an array.
        # Individual per-session files are the preferred input path.
        if isinstance(payload, list):
            combined_messages: list[NormalisedMessage] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                conv = _session_from_dict(item, path)
                if conv is None:
                    continue
                # Insert a separator message so different sessions stay
                # visually distinct when read in the GUI.
                title = conv.summary or conv.metadata.get("continue_session_id", "?")
                combined_messages.append(
                    NormalisedMessage(
                        role="system",
                        content=f"--- Continue.dev session: {title} ---",
                        created_at=conv.started_at,
                        metadata={"continue_session_id": conv.metadata.get("continue_session_id")},
                    )
                )
                combined_messages.extend(conv.messages)
            if not combined_messages:
                return None
            return NormalisedConversation(
                session_id=str(uuid.uuid5(_NS, f"continue:aggregate:{path}")),
                project_path="continue",
                model=None,
                entrypoint="continue.dev",
                source_tool="continue",
                started_at=combined_messages[0].created_at or datetime.now(timezone.utc),
                ended_at=combined_messages[-1].created_at,
                messages=combined_messages,
                summary="Continue.dev (combined sessions.json)",
                metadata={
                    "source": "continue",
                    "source_file": path.name,
                    "aggregate": True,
                    "session_count": len(payload),
                },
            )

        if isinstance(payload, dict):
            return _session_from_dict(payload, path)

        return None
