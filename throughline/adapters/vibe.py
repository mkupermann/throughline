"""Vibe (Mistral AI) adapter.

Reads Vibe session data from ~/.vibe/logs/session/ directories.
Vibe sessions are stored as:
- meta.json: Session metadata (session_id, start_time, end_time, environment, stats, etc.)
- messages.jsonl: Individual messages with role, content, tool_calls, reasoning_content, etc.

Each session directory matches the pattern: session_YYYYMMDD_HHMMSS_ID/
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Adapter, NormalisedConversation, NormalisedMessage

# Stable namespace for UUID derivation
_NS = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")


def _stable_uuid(ns, raw, fallback: str) -> str:
    """See ``throughline.adapters.cursor._stable_uuid``."""
    if raw:
        try:
            return str(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            return str(uuid.uuid5(ns, f"{fallback}:{raw}"))
    return str(uuid.uuid5(ns, fallback))

# Role mapping from Vibe to Throughline roles
_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant", 
    "system": "system",
    "tool": "tool_result",
}

# Pattern to match Vibe session directories
# Examples: session_20260727_180500_c919513d, session_20260728_091450_c0d81646
_SESSION_DIR_PATTERN = re.compile(r'^session_\d{8}_\d{6}_[a-f0-9]+$')

# ANSI escape code pattern for cleaning content
_ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*m')


def _clean_ansi_content(content: str) -> str:
    """Remove ANSI escape codes from Vibe session content."""
    if not content:
        return ""
    # Remove ANSI color codes
    content = _ANSI_ESCAPE_PATTERN.sub("", content)
    # Remove any remaining escape characters
    content = content.replace("\x1b", "")
    return content


def _clean_ansi_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Clean ANSI codes from all string values in a dictionary."""
    if not isinstance(data, dict):
        return data
    cleaned = {}
    for key, value in data.items():
        if isinstance(value, str):
            cleaned[key] = _clean_ansi_content(value)
        elif isinstance(value, dict):
            cleaned[key] = _clean_ansi_from_dict(value)
        elif isinstance(value, list):
            cleaned[key] = [_clean_ansi_content(v) if isinstance(v, str) else v for v in value]
        else:
            cleaned[key] = value
    return cleaned


def _parse_timestamp(ts_str: str | None) -> datetime:
    """Parse Vibe timestamp format to datetime."""
    if not ts_str:
        return datetime.now(timezone.utc)
    
    try:
        # Handle ISO format with Z or timezone
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        pass
    
    # Try parsing without timezone (assume UTC)
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def _derive_session_id(session_id: str) -> str:
    """Derive a deterministic UUID for Vibe sessions."""
    if not session_id:
        return str(uuid.uuid4())
    return str(uuid.uuid5(_NS, f"vibe:{session_id}"))


def _extract_content_from_message(message: dict[str, Any]) -> tuple[str, Any | None]:
    """Extract plain text content and structured content blocks from a Vibe message."""
    parts = []
    content_blocks = None
    
    # Main content
    content = message.get("content", "")
    if content:
        if isinstance(content, str):
            cleaned = _clean_ansi_content(content)
            parts.append(cleaned)
            content_blocks = content  # Keep original for content_blocks
        elif isinstance(content, list):
            # Structured content (less common in Vibe, but possible)
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(_clean_ansi_content(str(block.get("text", ""))))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[Tool: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        tc = block.get("content", "")
                        if isinstance(tc, str):
                            parts.append(_clean_ansi_content(tc)[:500])
                elif isinstance(block, str):
                    parts.append(_clean_ansi_content(block))
            content_blocks = content
    
    # Reasoning content (from assistant) - use only if no regular content
    reasoning = message.get("reasoning_content", "")
    if reasoning and not parts:
        parts.append(_clean_ansi_content(reasoning))
        if content_blocks is None:
            content_blocks = reasoning
    
    text_content = "\n".join(parts)
    return text_content[:10000], content_blocks  # Limit content length


def _extract_tool_calls_from_message(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract tool calls from a Vibe message."""
    tool_calls = message.get("tool_calls", [])
    if not tool_calls or not isinstance(tool_calls, list):
        return None
    
    result = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            func = tc.get("function", {})
            if isinstance(func, dict):
                result.append({
                    "tool_name": func.get("name", ""),
                    "input": func.get("arguments", {}),
                })
    return result if result else None


def _get_model_from_message(message: dict[str, Any]) -> str | None:
    """Extract model from a Vibe message."""
    model = message.get("model")
    if model:
        return model
    return None


def _map_vibe_role(role: str | None) -> str:
    """Map Vibe role to Throughline message_role enum."""
    if not role:
        return "user"
    role = role.lower()
    return _ROLE_MAP.get(role, "user")


def _get_project_name_from_meta(meta: dict[str, Any]) -> str | None:
    """Extract project name from Vibe session metadata."""
    env = meta.get("environment", {})
    working_dir = env.get("working_directory", "")
    if working_dir:
        path_obj = Path(working_dir)
        if path_obj.name and path_obj.name != ".":
            return str(path_obj.name)
        if len(path_obj.parts) > 1:
            return str(path_obj.parts[-2]) if path_obj.parts[-2] else None
    return None


def _load_session_metadata(session_dir: Path) -> dict[str, Any] | None:
    """Load metadata from a Vibe session directory."""
    meta_path = session_dir / "meta.json"
    if not meta_path.exists():
        return None
    
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
            return _clean_ansi_from_dict(meta)
    except (json.JSONDecodeError, OSError):
        return None


def _load_session_messages(session_dir: Path) -> list[dict[str, Any]]:
    """Load all messages from a Vibe session directory."""
    messages_path = session_dir / "messages.jsonl"
    
    if not messages_path.exists():
        return []
    
    messages = []
    try:
        with open(messages_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    messages.append(_clean_ansi_from_dict(msg))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    
    return messages


def _parse_session_dir(session_dir: Path) -> dict[str, Any] | None:
    """Parse a single Vibe session directory into ``NormalisedConversation`` kwargs.

    Returns a plain dict rather than a ``NormalisedConversation`` so the
    caller (``VibeAdapter.parse``) supplies ``entrypoint`` and
    ``source_tool`` itself — keeping those two provider-identity fields
    visible at the adapter class, not buried in a module-level helper.
    """
    # Load metadata
    meta = _load_session_metadata(session_dir)
    if not meta:
        return None
    
    session_id_str = meta.get("session_id")
    if not session_id_str:
        return None
    
    # Load messages
    messages = _load_session_messages(session_dir)
    if not messages:
        return None
    
    # Derive session UUID
    session_uuid = _derive_session_id(session_id_str)
    
    # Parse timestamps
    start_time = _parse_timestamp(meta.get("start_time"))
    end_time = _parse_timestamp(meta.get("end_time"))
    
    # Extract model from messages or meta
    model = None
    for msg in messages:
        model = _get_model_from_message(msg)
        if model:
            break
    
    if not model:
        model = meta.get("model") or meta.get("active_model")
    
    # Get project name
    project_path = _get_project_name_from_meta(meta)
    
    # Extract stats
    stats = meta.get("stats", {})
    token_count_in = stats.get("session_prompt_tokens") or stats.get("total_prompt_tokens")
    token_count_out = stats.get("session_completion_tokens") or stats.get("total_completion_tokens")
    
    # Get session title
    title = meta.get("title")
    
    # Convert messages to NormalisedMessage format
    normalised_messages = []
    for msg in messages:
        text_content, content_blocks = _extract_content_from_message(msg)
        tool_calls = _extract_tool_calls_from_message(msg)
        
        normalised_msg = NormalisedMessage(
            role=_map_vibe_role(msg.get("role")),
            content=text_content,
            content_blocks=content_blocks,
            tool_calls=tool_calls,
            tool_name=tool_calls[0]["tool_name"] if tool_calls and len(tool_calls) > 0 else None,
            created_at=_parse_timestamp(msg.get("timestamp")),
            model=_get_model_from_message(msg),
            is_sidechain=msg.get("injected", False),  # Vibe uses "injected" for compaction context
            # Vibe numbers its messages `msg_1`, `msg_2`, … and the column is
            # a uuid. Same defect as the Cursor and Zed adapters, found the
            # same way; see cursor._stable_uuid for why this is not cosmetic.
            parent_uuid=(
                _stable_uuid(_NS, msg.get("parent_message_id"), f"vibe:{session_uuid}:msg")
                if msg.get("parent_message_id") else None
            ),
            uuid=_stable_uuid(
                _NS, msg.get("message_id"), f"vibe:{session_uuid}:msg:{len(normalised_messages)}"
            ),
            metadata={
                "injected": msg.get("injected", False),
                "reasoning_content": _clean_ansi_content(msg.get("reasoning_content", ""))[:2000],
                "reasoning_message_id": msg.get("reasoning_message_id"),
                "tool_call_id": msg.get("tool_call_id"),
            } if msg.get("injected") else {},
        )
        normalised_messages.append(normalised_msg)
    
    # Build metadata for the conversation
    vibe_metadata = {
        "source": "vibe",
        "vibe_session_id": session_id_str,
        "parent_session_id": meta.get("parent_session_id"),
        "username": meta.get("username"),
        "title": title,
        "title_source": meta.get("title_source"),
        "git_commit": meta.get("git_commit"),
        "git_branch": meta.get("git_branch"),
        "stats": stats,
        "environment": meta.get("environment", {}),
        "tools_available_count": len(meta.get("tools_available", [])),
    }
    
    return {
        "session_id": session_uuid,
        "project_path": project_path,
        "model": model,
        "started_at": start_time,
        "ended_at": end_time,
        "messages": normalised_messages,
        "git_branch": meta.get("git_branch"),  # May be None
        "token_count_in": token_count_in,
        "token_count_out": token_count_out,
        "summary": title[:500] if title else None,
        "metadata": vibe_metadata,
    }


class VibeAdapter(Adapter):
    """Vibe (Mistral AI) session adapter.
    
    Ingests Vibe sessions from ~/.vibe/logs/session/ directories.
    Vibe is Mistral AI's CLI coding agent similar to Claude Code.
    """

    name = "vibe"
    label = "Vibe (Mistral AI)"
    home = Path("~/.vibe/logs/session").expanduser()

    def discover(self) -> Iterable[Path]:
        """Discover Vibe session directories."""
        home = self.home
        if not home.exists():
            return []
        
        session_dirs = []
        for entry in home.iterdir():
            if entry.is_dir() and _SESSION_DIR_PATTERN.match(entry.name):
                session_dirs.append(entry)
        
        return sorted(session_dirs, key=lambda x: x.name)

    def parse(self, path: Path) -> "NormalisedConversation | list[NormalisedConversation] | None":
        """Parse a Vibe session directory.
        
        Each session directory contains meta.json and messages.jsonl files.
        Returns a single NormalisedConversation for the session.
        """
        if not path.is_dir():
            return None

        fields = _parse_session_dir(path)
        if fields is None:
            return None
        return NormalisedConversation(
            entrypoint="",  # Not available in Vibe
            source_tool=self.name,
            **fields,
        )