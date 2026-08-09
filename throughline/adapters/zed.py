"""Zed adapter.

Zed stores sessions in ~/.zed/data/sessions/ as JSON files.

Zed is a high-performance, collaborative code editor from Atom’s creators.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Adapter, NormalisedConversation, NormalisedMessage

# Stable namespace for UUID derivation
_ZED_NS = uuid.UUID("8f4e2d1c-7b9a-4e3d-8c6b-1f2e3d4c5b6a")

# Role mapping from Zed to Throughline
_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "system": "system",
}


def _derive_session_id(session_id: str) -> str:
    """Derive a deterministic UUID for Zed sessions."""
    if not session_id:
        return str(uuid.uuid4())
    return str(uuid.uuid5(_ZED_NS, f"zed:{session_id}"))


def _map_zed_role(role: str | None) -> str:
    """Map Zed role to Throughline role."""
    if not role:
        return "user"
    role = role.lower()
    return _ROLE_MAP.get(role, "user")


def _parse_timestamp(ts: str | float | int | None) -> datetime | None:
    """Parse Zed timestamp to datetime."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Zed uses Unix timestamps in milliseconds
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _extract_content(message: dict[str, Any]) -> str:
    """Extract content from a Zed message."""
    content = message.get("content", "")
    if isinstance(content, list):
        # Handle content blocks (Zed may use structured content)
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "code":
                    parts.append(f"```{block.get('language', '')}\n{block.get('text', '')}\n```")
            elif isinstance(block, str):
                parts.append(block)
        content = "\n".join(parts)
    return content[:10000]


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract tool calls from a Zed message."""
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


def _get_project_path(session_data: dict[str, Any]) -> str | None:
    """Extract project path from Zed session data."""
    workspace = session_data.get("workspace")
    if workspace:
        path_obj = Path(workspace)
        if path_obj.name and path_obj.name != ".":
            return str(path_obj.name)
        if len(path_obj.parts) > 1:
            return str(path_obj.parts[-2]) if path_obj.parts[-2] else None
    
    # Try project_root
    project_root = session_data.get("project_root")
    if project_root:
        path_obj = Path(project_root)
        if path_obj.name and path_obj.name != ".":
            return str(path_obj.name)
    
    return None


def _parse_zed_session_file(file_path: Path) -> NormalisedConversation | None:
    """Parse a single Zed session JSON file."""
    if not file_path.exists():
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    
    # Zed session structure
    messages = session_data.get("messages", [])
    if not messages:
        return None
    
    session_id = session_data.get("id") or file_path.stem
    if not session_id:
        return None
    
    # Derive session UUID
    session_uuid = _derive_session_id(session_id)
    
    # Extract metadata
    model = session_data.get("model")
    start_time = _parse_timestamp(session_data.get("started_at"))
    end_time = _parse_timestamp(session_data.get("ended_at"))
    project_path = _get_project_path(session_data)
    
    # Convert messages to NormalisedMessage
    normalised_messages = []
    for msg in messages:
        content = _extract_content(msg)
        tool_calls = _extract_tool_calls(msg)
        
        normalised_msg = NormalisedMessage(
            role=_map_zed_role(msg.get("role")),
            content=content,
            content_blocks=msg.get("content"),
            tool_calls=tool_calls,
            tool_name=tool_calls[0]["tool_name"] if tool_calls else None,
            created_at=_parse_timestamp(msg.get("timestamp")),
            model=msg.get("model") or model,
            is_sidechain=msg.get("is_sidechain", False),
            parent_uuid=msg.get("parent_message_id"),
            uuid=msg.get("id"),
            metadata={
                "zed_message_type": msg.get("type"),
            },
        )
        normalised_messages.append(normalised_msg)
    
    # Build metadata
    zed_metadata = {
        "source": "zed",
        "zed_session_id": session_id,
        "zed_version": session_data.get("version"),
        "workspace": session_data.get("workspace"),
    }
    
    # Token counts
    token_count_in = session_data.get("token_count_in")
    token_count_out = session_data.get("token_count_out")
    
    # Summary
    summary = session_data.get("title") or session_data.get("summary")
    
    return NormalisedConversation(
        session_id=session_uuid,
        project_path=project_path,
        model=model,
        entrypoint="zed",
        started_at=start_time,
        ended_at=end_time,
        messages=normalised_messages,
        git_branch=session_data.get("git_branch"),
        token_count_in=token_count_in,
        token_count_out=token_count_out,
        summary=summary[:500] if summary else None,
        metadata=zed_metadata,
    )


class ZedAdapter(Adapter):
    """Zed adapter for Throughline.
    
    Ingests Zed sessions from ~/.zed/data/sessions/*.json files.
    Zed is a high-performance, collaborative code editor.
    """
    
    name = "zed"
    label = "Zed"
    home = Path("~/.zed/data/sessions").expanduser()
    
    def discover(self) -> Iterable[Path]:
        """Discover Zed session JSON files."""
        home = self.home
        if not home.exists():
            return []
        
        session_files = []
        for entry in home.iterdir():
            if entry.is_file() and entry.suffix == ".json":
                # Zed session files are named like: session_<id>.json
                if entry.name.startswith("session_") or "session" in entry.name.lower():
                    session_files.append(entry)
        
        return sorted(session_files, key=lambda x: x.stat().st_mtime, reverse=True)
    
    def parse(self, path: Path) -> "NormalisedConversation | list[NormalisedConversation] | None":
        """Parse a Zed session JSON file."""
        if not path.is_file():
            return None
        
        return _parse_zed_session_file(path)
