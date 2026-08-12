"""Cursor adapter.

Cursor stores sessions in ~/.cursor/sessions/ as JSONL files,
similar to Claude Code's format.

Cursor is an AI-powered code editor built on VS Code.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Adapter, NormalisedConversation, NormalisedMessage

# Stable namespace for UUID derivation
_CURSOR_NS = uuid.UUID("5e3f7a8b-2c1d-4e9f-8a6b-0c5d2e4f3a7b")

# Role mapping from Cursor to Throughline
_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
}


def _derive_session_id(session_hash: str) -> str:
    """Derive a deterministic UUID for Cursor sessions."""
    if not session_hash:
        return str(uuid.uuid4())
    return str(uuid.uuid5(_CURSOR_NS, f"cursor:{session_hash}"))


def _map_cursor_role(role: str | None) -> str:
    """Map Cursor role to Throughline role."""
    if not role:
        return "user"
    role = role.lower()
    return _ROLE_MAP.get(role, "user")


def _parse_timestamp(ts: str | float | int | None) -> datetime | None:
    """Parse Cursor timestamp to datetime."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _extract_content(message: dict[str, Any]) -> str:
    """Extract content from a Cursor message."""
    content = message.get("content", "")
    if isinstance(content, list):
        # Handle content blocks
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "code":
                    language = block.get("language", "")
                    code_text = block.get("text", "")
                    parts.append(f"```{language}\n{code_text}\n```")
            elif isinstance(block, str):
                parts.append(block)
        content = "\n".join(parts)
    return content[:10000]  # Limit content length


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract tool calls from a Cursor message."""
    tool_calls = message.get("tool_calls", [])
    if not tool_calls:
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


def _get_project_path(session_dir: Path) -> str | None:
    """Extract project path from Cursor session metadata."""
    # Cursor doesn't store project path in session files
    # Try to infer from cwd if available
    cwd = None
    
    # Check for a metadata file
    for file in session_dir.iterdir():
        if file.suffix == ".json" and file.name != "session.json":
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cwd = data.get("cwd") or data.get("working_directory")
                    if cwd:
                        break
            except (json.JSONDecodeError, OSError):
                continue
    
    if cwd:
        path_obj = Path(cwd)
        if path_obj.name and path_obj.name != ".":
            return str(path_obj.name)
        if len(path_obj.parts) > 1:
            return str(path_obj.parts[-2]) if path_obj.parts[-2] else None
    
    return None


def _parse_cursor_file(file_path: Path, source_tool: str) -> NormalisedConversation | None:
    """Parse a single Cursor session JSONL file."""
    if not file_path.exists():
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            messages_data = [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return None
    
    if not messages_data:
        return None
    
    # Extract session metadata from first message or file name
    first_msg = messages_data[0]
    session_hash = first_msg.get("session_hash") or file_path.stem
    
    if not session_hash:
        return None
    
    # Derive session UUID
    session_uuid = _derive_session_id(session_hash)
    
    # Extract model
    model = None
    for msg in messages_data:
        model = msg.get("model")
        if model:
            break
    
    # Get timestamps
    start_time = None
    end_time = None
    for msg in messages_data:
        ts = msg.get("timestamp")
        if ts:
            parsed = _parse_timestamp(ts)
            if parsed:
                if not start_time or parsed < start_time:
                    start_time = parsed
                if not end_time or parsed > end_time:
                    end_time = parsed
    
    # If no timestamps, use file modification time
    if not start_time:
        start_time = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    
    # Get project path
    project_path = _get_project_path(file_path.parent)
    
    # Convert messages to NormalisedMessage
    normalised_messages = []
    for msg in messages_data:
        content = _extract_content(msg)
        tool_calls = _extract_tool_calls(msg)
        
        normalised_msg = NormalisedMessage(
            role=_map_cursor_role(msg.get("role")),
            content=content,
            content_blocks=msg.get("content"),
            tool_calls=tool_calls,
            tool_name=tool_calls[0]["tool_name"] if tool_calls else None,
            created_at=_parse_timestamp(msg.get("timestamp")),
            model=msg.get("model"),
            is_sidechain=False,
            parent_uuid=msg.get("parent_message_id"),
            uuid=msg.get("message_id"),
            metadata={},
        )
        normalised_messages.append(normalised_msg)
    
    # Build metadata
    cursor_metadata = {
        "source": "cursor",
        "cursor_session_hash": session_hash,
        "file_path": str(file_path),
    }
    
    # Token counts (if available)
    token_count_in = None
    token_count_out = None
    
    return NormalisedConversation(
        session_id=session_uuid,
        project_path=project_path,
        model=model,
        entrypoint="cursor",
        source_tool=source_tool,
        started_at=start_time,
        ended_at=end_time,
        messages=normalised_messages,
        git_branch=None,
        token_count_in=token_count_in,
        token_count_out=token_count_out,
        summary=None,
        metadata=cursor_metadata,
    )


class CursorAdapter(Adapter):
    """Cursor adapter for Throughline.
    
    Ingests Cursor sessions from ~/.cursor/sessions/*.jsonl files.
    Cursor is an AI-powered VS Code fork by Anysphere.
    """
    
    name = "cursor"
    label = "Cursor"
    home = Path("~/.cursor/sessions").expanduser()
    
    def discover(self) -> Iterable[Path]:
        """Discover Cursor session JSONL files."""
        home = self.home
        if not home.exists():
            return []
        
        session_files = []
        for entry in home.iterdir():
            if entry.is_file() and entry.suffix == ".jsonl":
                session_files.append(entry)
        
        return sorted(session_files, key=lambda x: x.name)
    
    def parse(self, path: Path) -> "NormalisedConversation | list[NormalisedConversation] | None":
        """Parse a Cursor session JSONL file."""
        if not path.is_file():
            return None

        return _parse_cursor_file(path, source_tool=self.name)
