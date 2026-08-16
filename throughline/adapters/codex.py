"""OpenAI Codex CLI (~/.codex/sessions/) adapter.

The Codex CLI writes one JSONL file per session under
``~/.codex/sessions/<YYYY-MM-DD>/rollout-<timestamp>-<id>.jsonl``.
Each line is an event. The current schema (as of late 2025) is:

    {"type": "session_meta",     "session_id": "...", "model": "...", ...}
    {"type": "user_message",     "content": "..."}
    {"type": "assistant_message", "content": "...", "model": "..."}
    {"type": "tool_call",        "name": "shell", "arguments": {...}}
    {"type": "tool_result",      "tool_call_id": "...", "output": "..."}

The schema has evolved a few times in Codex's short life; this adapter
parses defensively — unknown keys become metadata, unknown event types are
skipped. There's no Codex installation on the machine I was written on,
so the integration is covered by unit tests against synthetic fixtures
(see tests/test_adapter_codex.py).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Adapter, NormalisedConversation, NormalisedMessage

_NS = uuid.UUID("0c7f9a3a-1d8b-4b2e-9c4a-1b2d3e4f5a6b")


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Codex sometimes emits ms-since-epoch.
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


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False)[:500])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)[:2000]


class CodexAdapter(Adapter):
    name = "codex"
    label = "OpenAI Codex CLI"
    home = Path("~/.codex/sessions").expanduser()

    def discover(self) -> Iterable[Path]:
        if not self.home.exists():
            return []
        # Codex layout: ~/.codex/sessions/<YYYY-MM-DD>/rollout-*.jsonl
        return sorted(self.home.rglob("rollout-*.jsonl"))

    def parse(self, path: Path) -> NormalisedConversation | None:
        events: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return None
        if not events:
            return None

        # Pull session-level metadata from any session_meta-shaped event,
        # falling back to fields on the first event.
        meta_event: dict[str, Any] = {}
        for ev in events:
            if ev.get("type") in ("session_meta", "session", "config"):
                meta_event = ev
                break
        if not meta_event:
            meta_event = events[0]

        raw_session_id = meta_event.get("session_id") or meta_event.get("id") or path.stem
        model = meta_event.get("model") or meta_event.get("model_name")
        cwd = meta_event.get("cwd") or meta_event.get("working_directory")
        project_path = cwd or "codex"

        # Timestamps — prefer explicit started_at / first event timestamp.
        started = (
            _parse_ts(meta_event.get("started_at"))
            or _parse_ts(meta_event.get("timestamp"))
            or _parse_ts(events[0].get("timestamp"))
            or datetime.now(timezone.utc)
        )
        ended = _parse_ts(meta_event.get("ended_at")) or _parse_ts(events[-1].get("timestamp")) or started

        norm: list[NormalisedMessage] = []
        for idx, ev in enumerate(events):
            ev_type = (ev.get("type") or "").lower()
            ts = _parse_ts(ev.get("timestamp")) or started

            if ev_type in ("user_message", "user", "input"):
                norm.append(
                    NormalisedMessage(
                        role="user",
                        content=_stringify(ev.get("content") or ev.get("text")),
                        created_at=ts,
                        uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                        metadata={k: v for k, v in ev.items() if k not in ("type", "content", "text")},
                    )
                )
            elif ev_type in ("assistant_message", "assistant", "output", "response"):
                norm.append(
                    NormalisedMessage(
                        role="assistant",
                        content=_stringify(ev.get("content") or ev.get("text")),
                        created_at=ts,
                        model=ev.get("model") or model,
                        token_count=ev.get("token_count") or ev.get("tokens"),
                        uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                        metadata={k: v for k, v in ev.items() if k not in ("type", "content", "text")},
                    )
                )
            elif ev_type in ("tool_call", "function_call"):
                norm.append(
                    NormalisedMessage(
                        role="assistant",
                        content=f"[Tool: {ev.get('name', '?')}] {_stringify(ev.get('arguments'))[:500]}",
                        created_at=ts,
                        tool_calls=[{"tool_name": ev.get("name"), "input": ev.get("arguments")}],
                        tool_name=ev.get("name"),
                        uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                        metadata={k: v for k, v in ev.items() if k not in ("type", "name", "arguments")},
                    )
                )
            elif ev_type in ("tool_result", "function_result"):
                norm.append(
                    NormalisedMessage(
                        role="tool_result",
                        content=_stringify(ev.get("output") or ev.get("content")),
                        created_at=ts,
                        tool_name=ev.get("name"),
                        uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                        metadata={k: v for k, v in ev.items() if k not in ("type", "output", "content")},
                    )
                )
            elif ev_type in ("system_message", "system"):
                norm.append(
                    NormalisedMessage(
                        role="system",
                        content=_stringify(ev.get("content") or ev.get("text")),
                        created_at=ts,
                        uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                    )
                )
            # else: unknown event type — skip silently.

        if not norm:
            return None

        return NormalisedConversation(
            session_id=str(uuid.uuid5(_NS, f"codex:{raw_session_id}")),
            project_path=project_path,
            model=model,
            entrypoint="codex",
            source_tool="codex",
            started_at=started,
            ended_at=ended,
            messages=norm,
            metadata={
                "source": "codex",
                "codex_session_id": raw_session_id,
                "rollout_file": path.name,
            },
        )
