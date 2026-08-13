"""Hermes Agent adapter.

Reads from two sources, in this order of authority:

1. ``~/.hermes/state.db`` — the SQLite database Hermes writes to in real
   time. Schema: ``sessions(id, source, model, title, started_at,
   message_count, …)`` and ``messages(session_id, role, content,
   timestamp, tool_calls, reasoning_*, finish_reason, …)``. Richer than
   the JSON exports — a single Hermes session may have 28 messages here
   while only 6 appear in the exported JSON file. The DB is treated as
   a single "file" by the writer, but ``parse()`` returns N
   conversations (one per session row).
2. ``~/.hermes/sessions/session_*.json`` — exported session snapshots.
   Kept so users who have JSON exports but not state.db still get
   ingested, and so the archival format stays a supported source.

UUID derivation is identical for both paths (``uuid5`` over
``hermes:<session_id>``), so the same session in both places resolves to
the same ``conversations.session_id`` and upserts cleanly. The DB is
discovered first, so it wins on conflicts.
"""

from __future__ import annotations

import json
import sqlite3
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


def _parse_ts(s: str | float | int | None) -> datetime:
    """A timestamp from either shape Hermes writes, or now() as a last resort.

    Hermes emits `session_start` as a unix number in its JSON exports and as an
    ISO string elsewhere. This only handled the string: `fromisoformat(float)`
    raises TypeError, which the except swallowed, and the function returned the
    *current time* — so a session exported months ago was dated to the moment it
    happened to be imported. Every message then inherited that, because the
    JSON path stamped them all with the session start, and a whole transcript
    collapsed onto one instant.

    Falling back to now() at all is a compromise worth keeping — a session with
    no usable date is still worth storing — but it must be the last branch, not
    the one a well-formed number lands in.
    """
    if s is None or s == "":
        return datetime.now(timezone.utc)
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return _from_unix(s) or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        # A bare number arriving as a string ("1700000000") is still a date.
        try:
            return _from_unix(float(s)) or datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)


#: Keys Hermes has used for a message's own timestamp, in the order tried.
_MSG_TIME_KEYS = ("timestamp", "created_at", "time", "ts")


def _msg_time(msg: dict) -> datetime | None:
    """A message's own timestamp, whichever key and shape it arrived in."""
    for key in _MSG_TIME_KEYS:
        raw = msg.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            got = _from_unix(raw)
        else:
            try:
                got = datetime.fromisoformat(str(raw))
                if got.tzinfo is None:
                    got = got.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                got = None
        if got is not None:
            return got
    return None


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


def _parse_state_db(path: Path) -> list[NormalisedConversation]:
    """Pull every session row out of Hermes's state.db SQLite file.

    Returns an empty list on schema mismatch or read errors so a stale
    Hermes install never crashes the ingest pipeline. Each returned
    NormalisedConversation has the same uuid5-derived session_id as the
    matching ``sessions/session_<id>.json`` file would, so DB ingestion
    upserts cleanly on top of (or under) the JSON path.
    """
    out: list[NormalisedConversation] = []
    # ``immutable=1`` opens read-only and won't lock or modify the file
    # even while Hermes is writing. ``mode=ro`` plus ``immutable`` is
    # the canonical "snapshot read" combo.
    try:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro&immutable=1", uri=True
        )
    except sqlite3.Error:
        return out
    conn.row_factory = sqlite3.Row
    try:
        # Probe the schema cheaply — if either table is missing, the
        # state.db is from a Hermes version we don't understand and we
        # bail rather than emit garbage.
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "sessions" not in names or "messages" not in names:
            return out

        sessions = list(
            conn.execute(
                "SELECT id, source, model, title, started_at, ended_at, "
                "       system_prompt, message_count, "
                "       input_tokens, output_tokens, "
                "       actual_cost_usd, estimated_cost_usd "
                "FROM sessions"
            )
        )

        for s in sessions:
            sid = s["id"]
            if not sid:
                continue
            started = _from_unix(s["started_at"])
            ended = _from_unix(s["ended_at"]) or started

            msgs = list(
                conn.execute(
                    "SELECT role, content, tool_call_id, tool_calls, tool_name, "
                    "       timestamp, token_count, finish_reason, "
                    "       reasoning, reasoning_content, reasoning_details "
                    "FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (sid,),
                )
            )

            norm: list[NormalisedMessage] = []
            for idx, m in enumerate(msgs):
                role = _ROLE_MAP.get((m["role"] or "").lower())
                if role is None:
                    continue
                content = m["content"] or ""
                # tool_calls is stored as a JSON string blob.
                tool_calls_raw = m["tool_calls"]
                tool_calls: list[dict] | None = None
                if tool_calls_raw:
                    try:
                        parsed = json.loads(tool_calls_raw)
                        if isinstance(parsed, list):
                            tool_calls = [
                                {
                                    "tool_name": (
                                        (c.get("function") or {}).get("name")
                                        if isinstance(c, dict) else None
                                    ),
                                    "input": (
                                        (c.get("function") or {}).get("arguments")
                                        if isinstance(c, dict) else None
                                    ),
                                }
                                for c in parsed
                            ]
                    except (json.JSONDecodeError, TypeError):
                        pass
                meta = {
                    k: m[k]
                    for k in (
                        "finish_reason",
                        "reasoning",
                        "reasoning_content",
                        "reasoning_details",
                        "tool_call_id",
                    )
                    if m[k] is not None
                }
                norm.append(
                    NormalisedMessage(
                        role=role,
                        content=content,
                        tool_calls=tool_calls,
                        tool_name=m["tool_name"] or (
                            tool_calls[0]["tool_name"] if tool_calls else None
                        ),
                        created_at=_from_unix(m["timestamp"]) or started,
                        model=s["model"] if role == "assistant" else None,
                        token_count=m["token_count"],
                        uuid=str(uuid.uuid5(_NS, f"hermes:{sid}:msg:{idx}")),
                        metadata=meta,
                    )
                )
            if not norm:
                continue

            out.append(
                NormalisedConversation(
                    session_id=str(uuid.uuid5(_NS, f"hermes:{sid}")),
                    project_path="hermes",
                    model=s["model"],
                    entrypoint=s["source"] or "hermes",
                    source_tool="hermes",
                    started_at=started,
                    ended_at=ended,
                    messages=norm,
                    summary=s["title"],
                    token_count_in=s["input_tokens"] or None,
                    token_count_out=s["output_tokens"] or None,
                    metadata={
                        "source": "hermes",
                        "origin": "state.db",
                        "hermes_session_id": sid,
                        "system_prompt_chars": len(s["system_prompt"] or ""),
                        "actual_cost_usd": s["actual_cost_usd"],
                        "estimated_cost_usd": s["estimated_cost_usd"],
                    },
                )
            )
    except sqlite3.Error:
        # Any read error → return what we have (possibly empty). Don't
        # raise; the JSON export path may still have data.
        pass
    finally:
        conn.close()
    return out


def _from_unix(value: Any) -> datetime | None:
    """Convert a Hermes Unix timestamp (float seconds) to UTC datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


class HermesAdapter(Adapter):
    name = "hermes"
    label = "Hermes Agent"
    # Reported home — the most user-visible directory. ``discover`` walks
    # the parent ``~/.hermes/`` for both state.db and sessions/.
    home = Path("~/.hermes/sessions").expanduser()

    @property
    def _hermes_root(self) -> Path:
        return Path("~/.hermes").expanduser()

    def is_present(self) -> bool:
        """At least one candidate file — ``state.db`` or a session export — exists.

        Was "the db file exists OR the sessions directory exists" — an
        empty ``~/.hermes/sessions/`` (dir created, nothing exported yet)
        reported present with nothing to ingest. ``discover()`` already
        enumerates both sources (it appends ``state.db`` itself when
        present, and globs ``session_*.json`` under ``sessions/``), so
        deriving from it covers both without duplicating the enumeration,
        matching the "at least one file discovered" contract from Task 5.
        A present-but-empty ``state.db`` still counts, same as Task 5's
        rule for claude_code: "discovered" means the writer has a candidate
        file, not that it necessarily parses into a conversation.
        """
        return any(True for _ in self.discover())

    def discover(self) -> Iterable[Path]:
        out: list[Path] = []
        db = self._hermes_root / "state.db"
        if db.exists():
            out.append(db)
        sessions = self._hermes_root / "sessions"
        if sessions.exists():
            out.extend(sorted(sessions.glob("session_*.json")))
        return out

    def parse(self, path: Path) -> "NormalisedConversation | list[NormalisedConversation] | None":
        # state.db path: emit one conversation per session row.
        if path.name == "state.db":
            convs = _parse_state_db(path)
            return convs if convs else None

        # JSON export path (unchanged from v0.3.0 behaviour).
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
                    # The message's own time when it has one. This read
                    # `created_at=started` unconditionally, so every message in
                    # a session shared a single timestamp — the transcript had
                    # no chronology and the timeline placed the whole
                    # conversation on one instant. The SQLite path opposite has
                    # always done this correctly; only the JSON export did not.
                    created_at=_msg_time(msg) or started,
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
            source_tool="hermes",
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
