"""Cline (`saoudrizwan.claude-dev` VS Code extension) adapter.

Cline stores one directory per "task" (a top-level conversation), each with:

- ``api_conversation_history.json`` — the Anthropic-style message stream
  the underlying model actually sees. Same shape as Claude Code JSONL
  messages: ``[{role, content}, ...]`` where content is a string or an
  array of ``{type: text/tool_use/tool_result, ...}`` blocks.
- ``ui_messages.json`` — the UI-rendered stream (says, asks,
  command-approval prompts). Lossy compared to the API history, used as
  a fallback when the API file is missing.
- ``task_metadata.json`` — ``{id, ts, task, ...}`` with the original
  prompt and a Unix-ms timestamp.

The task directory itself lives under one of several historical paths;
we check all of them. There is no Cline installation actively running
sessions on the machine I was written on, so the test surface is unit
tests against synthetic fixtures.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Adapter, NormalisedConversation, NormalisedMessage

_NS = uuid.UUID("8a7b6c5d-4e3f-4a2b-9c8d-7e6f5a4b3c2d")

_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool_result",
    "function": "tool_result",
}


# Candidate roots for the tasks directory. Cline has shipped under a few
# storage layouts: the original VS Code globalStorage path, the Cursor
# variant (Cursor reuses VS Code's extension API), and a newer
# ``~/.cline/data/tasks`` location that some forks/versions adopted.
def _candidate_task_roots() -> list[Path]:
    home = Path.home()
    return [
        home / "Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
        home / "Library/Application Support/Cursor/User/globalStorage/saoudrizwan.claude-dev/tasks",
        # Newer / forked layouts:
        home / ".cline/data/tasks",
        home / ".cline/tasks",
        # Linux equivalent (~/.config/Code/...) — rare but cheap to include:
        home / ".config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
        # Windows: VS Code keeps globalStorage under %APPDATA%, which is
        # ~/AppData/Roaming. Listing it costs one stat call on the platforms
        # that do not have it, and its absence meant a Windows machine had a
        # Cline history the adapter simply could not see.
        home / "AppData/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
        home / "AppData/Roaming/Cursor/User/globalStorage/saoudrizwan.claude-dev/tasks",
    ]


def _parse_ts_ms(ms: Any) -> datetime | None:
    if ms is None:
        return None
    try:
        secs = float(ms) / 1000.0 if float(ms) > 1_000_000_000_000 else float(ms)
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _stringify(content: Any) -> tuple[str, Any]:
    """Render Cline's mixed content (string or block array) to plain text."""
    if content is None:
        return "", None
    if isinstance(content, str):
        return content, None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            t = block.get("type")
            if t == "text":
                parts.append(str(block.get("text", "")))
            elif t == "tool_use":
                # Cline tool_use blocks carry name + input. Render a
                # human-readable marker; the structured payload is kept
                # under content_blocks for downstream consumers.
                parts.append(f"[Tool: {block.get('name', '?')}]")
            elif t == "tool_result":
                tc = block.get("content")
                if isinstance(tc, str):
                    parts.append(tc[:500])
                elif isinstance(tc, list):
                    for inner in tc:
                        if isinstance(inner, dict) and inner.get("type") == "text":
                            parts.append(str(inner.get("text", ""))[:500])
            elif t == "image":
                parts.append("[image]")
        return "\n".join(parts), content
    return json.dumps(content, ensure_ascii=False)[:2000], None


def _parse_api_history(messages_raw: list, session_id: str) -> list[NormalisedMessage]:
    out: list[NormalisedMessage] = []
    for idx, msg in enumerate(messages_raw):
        if not isinstance(msg, dict):
            continue
        role = _ROLE_MAP.get((msg.get("role") or "").lower())
        if role is None:
            continue
        text, blocks = _stringify(msg.get("content"))
        # Tool calls — pull from content_blocks when present.
        tool_calls: list[dict] | None = None
        tool_name: str | None = None
        if isinstance(blocks, list):
            calls = [
                {"tool_name": b.get("name"), "input": b.get("input")}
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if calls:
                tool_calls = calls
                tool_name = calls[0].get("tool_name")
        out.append(
            NormalisedMessage(
                role=role,
                content=text,
                content_blocks=blocks,
                tool_calls=tool_calls,
                tool_name=tool_name,
                uuid=str(uuid.uuid5(_NS, f"cline:{session_id}:msg:{idx}")),
            )
        )
    return out


def _parse_ui_messages(messages_raw: list, session_id: str) -> list[NormalisedMessage]:
    """Fallback when only ui_messages.json is present.

    ``ui_messages.json`` is a flatter shape:
        [{"ts": 1234..., "type": "say"|"ask", "say"|"ask": "<subtype>", "text": "..."}, ...]

    ``say`` types include 'text', 'tool', 'api_req_started', 'command' etc.
    ``ask`` types are the model asking the user to approve something. We
    map both to user/assistant rows based on whether the model or the
    human originated them — and skip clearly-not-conversational types.
    """
    out: list[NormalisedMessage] = []
    skip_subtypes = {"api_req_started", "api_req_finished", "api_req_retried", "checkpoint_created"}
    for idx, m in enumerate(messages_raw):
        if not isinstance(m, dict):
            continue
        msg_type = m.get("type")
        text = m.get("text") or ""
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        ts = _parse_ts_ms(m.get("ts"))
        # 'say' is the model talking to the user (or echoing tool output);
        # 'ask' is the model asking for approval — also assistant-shaped.
        if msg_type == "say":
            subtype = m.get("say") or "text"
            if subtype in skip_subtypes:
                continue
            role = "assistant"
            if subtype == "user_feedback":
                role = "user"
        elif msg_type == "ask":
            role = "assistant"
        else:
            continue
        if not text.strip():
            continue
        out.append(
            NormalisedMessage(
                role=role,
                content=text,
                created_at=ts,
                uuid=str(uuid.uuid5(_NS, f"cline:{session_id}:ui:{idx}")),
                metadata={k: v for k, v in m.items() if k not in ("text", "ts") and v is not None},
            )
        )
    return out


def _parse_task_dir(task_dir: Path, source_tool: str) -> NormalisedConversation | None:
    """Parse one Cline task directory into a NormalisedConversation."""
    meta_path = task_dir / "task_metadata.json"
    api_path = task_dir / "api_conversation_history.json"
    ui_path = task_dir / "ui_messages.json"

    raw_task_id = task_dir.name
    summary: str | None = None
    started: datetime | None = None

    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if isinstance(meta, dict):
            raw_task_id = str(meta.get("id") or raw_task_id)
            summary = meta.get("task") or None
            if summary and len(summary) > 200:
                summary = summary[:200]
            started = _parse_ts_ms(meta.get("ts"))

    # Prefer the API history (richer); fall back to UI messages.
    messages: list[NormalisedMessage] = []
    model: str | None = None
    if api_path.exists():
        try:
            payload = json.loads(api_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, list):
            messages = _parse_api_history(payload, raw_task_id)
        elif isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            messages = _parse_api_history(payload["messages"], raw_task_id)
            model = payload.get("model")

    if not messages and ui_path.exists():
        try:
            payload = json.loads(ui_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, list):
            messages = _parse_ui_messages(payload, raw_task_id)

    if not messages:
        return None

    if started is None:
        # Best effort: take the dir mtime.
        started = datetime.fromtimestamp(task_dir.stat().st_mtime, tz=timezone.utc)
    ended = started
    for m in messages:
        if m.created_at and (ended is None or m.created_at > ended):
            ended = m.created_at

    return NormalisedConversation(
        session_id=str(uuid.uuid5(_NS, f"cline:{raw_task_id}")),
        project_path="cline",
        model=model,
        entrypoint="cline",
        source_tool=source_tool,
        started_at=started,
        ended_at=ended,
        summary=summary,
        messages=messages,
        metadata={
            "source": "cline",
            "cline_task_id": raw_task_id,
            "task_dir": str(task_dir),
        },
    )


class ClineAdapter(Adapter):
    name = "cline"
    label = "Cline (VS Code extension)"
    # Reported in CLI listings as the canonical home; the adapter actually
    # walks every candidate location in _candidate_task_roots().
    home = Path("~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks").expanduser()

    def is_present(self) -> bool:
        """At least one task directory was found, across all candidate roots.

        Was "any candidate root directory exists" — a Cline install with an
        empty ``tasks/`` dir (e.g. the extension installed but never used)
        reported present with nothing to ingest. Cline's data can live under
        several historical roots (see ``_candidate_task_roots``), so this
        can't just fall back to the base class's ``home``-based default —
        that only checks one of them. Deriving from ``discover()`` (which
        already walks every root) keeps the multi-root awareness while
        matching the "at least one file discovered" contract from Task 5.
        """
        return any(True for _ in self.discover())

    def discover(self) -> Iterable[Path]:
        seen: set[Path] = set()
        for root in _candidate_task_roots():
            if not root.exists():
                continue
            for child in root.iterdir():
                if child.is_dir() and child not in seen:
                    seen.add(child)
        return sorted(seen)

    def parse(self, path: Path) -> NormalisedConversation | None:
        # ``path`` is a task directory; but the shared writer keys
        # idempotency on file-hash. Pick a stable file inside the dir to
        # hash. We override sha256_file below so the writer sees a hash
        # of the task's conversation file, not the directory itself.
        return _parse_task_dir(path, source_tool=self.name)

    @staticmethod
    def sha256_file(path: Path) -> str:
        """Hash the API conversation file (or UI fallback) inside the task dir.

        Cline tasks are *directories*; the writer hashes "the file" for
        idempotency. We hash whichever transcript file is present so
        re-runs detect "the task grew" correctly.
        """
        for name in ("api_conversation_history.json", "ui_messages.json"):
            candidate = path / name
            if candidate.is_file():
                return Adapter.sha256_file(candidate)
        # Empty / no-transcript dir — hash its name so we still produce
        # something, and the upper layer will skip on parse() returning None.
        import hashlib

        return hashlib.sha256(path.name.encode("utf-8")).hexdigest()
