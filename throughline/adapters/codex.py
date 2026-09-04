"""OpenAI Codex (~/.codex/sessions/) adapter.

Current Codex builds write one rollout JSONL per thread below dated folders.
Each line has a timestamp, a top-level event type, and a nested payload. Durable
messages and tool activity use ``response_item``; ``event_msg`` mirrors most of
that content for the live UI and is deliberately not imported a second time.

Older Codex CLI builds used flat ``session_meta``, ``user_message``,
``assistant_message``, ``tool_call`` and ``tool_result`` records. Both formats
remain supported. Unknown telemetry and reasoning events are skipped.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Adapter, NormalisedConversation, NormalisedMessage

_NS = uuid.UUID("0c7f9a3a-1d8b-4b2e-9c4a-1b2d3e4f5a6b")
_PARSER_REVISION = "2"


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
                if block.get("type") in ("text", "input_text", "output_text"):
                    parts.append(str(block.get("text", "")))
                elif block.get("type") in ("input_image", "encrypted_content"):
                    # Keep structured blocks on the message, but do not put
                    # image payloads or encrypted reasoning into searchable text.
                    continue
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
        # Layouts seen in the wild include both YYYY-MM-DD and YYYY/MM/DD.
        return sorted(self.home.rglob("rollout-*.jsonl"))

    def declined_ingestion_fingerprint(self, content_hash: str) -> str:
        """Include parser revision so v1 declines are reconsidered once."""
        value = f"codex:{_PARSER_REVISION}:{content_hash}"
        return hashlib.sha256(value.encode()).hexdigest()

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
        meta_outer: dict[str, Any] = {}
        meta_is_nested = False
        for ev in events:
            if ev.get("type") in ("session_meta", "session", "config"):
                meta_outer = ev
                payload = ev.get("payload")
                meta_is_nested = isinstance(payload, dict)
                meta_event = payload if meta_is_nested else ev
                break
        if not meta_event:
            meta_outer = events[0]
            meta_event = events[0]

        turn_context: dict[str, Any] = {}
        for ev in events:
            if ev.get("type") == "turn_context" and isinstance(ev.get("payload"), dict):
                turn_context = ev["payload"]
                break

        if meta_is_nested:
            # Current Codex builds use `session_id` for the shared app/window
            # session and `id` for this rollout thread. Using session_id here
            # collapses many independent rollout files into one conversation.
            raw_session_id = meta_event.get("id") or meta_event.get("session_id") or path.stem
        else:
            raw_session_id = meta_event.get("session_id") or meta_event.get("id") or path.stem
        model = meta_event.get("model") or meta_event.get("model_name") or turn_context.get("model")
        cwd = meta_event.get("cwd") or meta_event.get("working_directory") or turn_context.get("cwd")
        project_path = cwd or "codex"

        # Timestamps — prefer explicit started_at / first event timestamp.
        started = (
            _parse_ts(meta_event.get("started_at"))
            or _parse_ts(meta_event.get("timestamp"))
            or _parse_ts(meta_outer.get("timestamp"))
            or _parse_ts(events[0].get("timestamp"))
            or datetime.now(timezone.utc)
        )
        last_payload = events[-1].get("payload")
        last_payload = last_payload if isinstance(last_payload, dict) else {}
        ended = (
            _parse_ts(meta_event.get("ended_at"))
            or _parse_ts(events[-1].get("timestamp"))
            or _parse_ts(last_payload.get("timestamp"))
            or started
        )

        norm: list[NormalisedMessage] = []
        tool_names: dict[str, str] = {}
        for idx, ev in enumerate(events):
            ev_type = (ev.get("type") or "").lower()
            ts = _parse_ts(ev.get("timestamp")) or started

            payload = ev.get("payload")
            if ev_type == "response_item" and isinstance(payload, dict):
                item_type = (payload.get("type") or "").lower()
                if item_type == "message" and payload.get("role") in ("user", "assistant"):
                    role = payload["role"]
                    content_blocks = payload.get("content")
                    norm.append(
                        NormalisedMessage(
                            role=role,
                            content=_stringify(content_blocks or payload.get("text")),
                            content_blocks=content_blocks,
                            created_at=ts,
                            model=payload.get("model") or (model if role == "assistant" else None),
                            token_count=payload.get("token_count") or payload.get("tokens"),
                            uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                            metadata={k: v for k, v in payload.items() if k not in ("type", "role", "content", "text")},
                        )
                    )
                elif item_type == "agent_message":
                    content_blocks = payload.get("content")
                    norm.append(
                        NormalisedMessage(
                            role="assistant",
                            content=_stringify(content_blocks or payload.get("text")),
                            content_blocks=content_blocks,
                            created_at=ts,
                            model=model,
                            is_sidechain=True,
                            uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                            metadata={k: v for k, v in payload.items() if k not in ("type", "content", "text")},
                        )
                    )
                elif item_type in ("custom_tool_call", "function_call"):
                    name = str(payload.get("name") or "?")
                    call_id = payload.get("call_id")
                    if call_id:
                        tool_names[str(call_id)] = name
                    arguments = payload.get("input") if "input" in payload else payload.get("arguments")
                    norm.append(
                        NormalisedMessage(
                            role="assistant",
                            content=f"[Tool: {name}] {_stringify(arguments)[:500]}",
                            created_at=ts,
                            tool_calls=[{"tool_name": name, "input": arguments}],
                            tool_name=name,
                            model=model,
                            uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                            metadata={
                                k: v for k, v in payload.items() if k not in ("type", "name", "input", "arguments")
                            },
                        )
                    )
                elif item_type in ("custom_tool_call_output", "function_call_output"):
                    call_id = str(payload.get("call_id") or "")
                    norm.append(
                        NormalisedMessage(
                            role="tool_result",
                            content=_stringify(payload.get("output") or payload.get("content")),
                            created_at=ts,
                            tool_name=tool_names.get(call_id),
                            uuid=str(uuid.uuid5(_NS, f"codex:{raw_session_id}:msg:{idx}")),
                            metadata={k: v for k, v in payload.items() if k not in ("type", "output", "content")},
                        )
                    )
                # event_msg mirrors most durable response_items. Reasoning,
                # telemetry and developer instructions are not transcript data.
                continue

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
