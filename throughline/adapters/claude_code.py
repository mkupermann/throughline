"""Claude Code (~/.claude/projects/<slug>/*.jsonl) adapter.

The existing ``scripts/ingest_sessions.py`` is the source of truth for
the parsing details (role mapping, content-block extraction, token
aggregation). This adapter re-uses those helpers so we don't fork
behaviour.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Adapter, NormalisedConversation, NormalisedMessage

# Sessions whose first user message starts with this marker are headless
# `claude -p ...` calls issued by scripts/generate_titles.py. Each such call is
# itself logged as a Claude Code session, then re-ingested here — producing
# hundreds of indistinguishable "Session-Titel-Generator" rows. Skip them at
# the adapter boundary so they never enter the DB.
_TITLE_GENERATOR_MARKER = (
    "Du bekommst einen Auszug aus einer Claude Code Session. "
    "Generiere einen prägnanten deutschen Titel"
)


def _is_title_generator_session(entries: list[dict[str, Any]]) -> bool:
    for e in entries:
        msg = e.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        text: str | None = None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    break
        if text and text.lstrip().startswith(_TITLE_GENERATOR_MARKER):
            return True
        return False
    return False


def _load_legacy() -> Any:
    """Import scripts/ingest_sessions.py as a module without running its main()."""
    root = Path(__file__).resolve().parents[2]
    scripts_dir = root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "_throughline_legacy_ingest_sessions",
        scripts_dir / "ingest_sessions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ClaudeCodeAdapter(Adapter):
    name = "claude_code"
    label = "Claude Code"
    home = Path("~/.claude/projects").expanduser()

    def discover(self) -> Iterable[Path]:
        if not self.home.exists():
            return []
        out: list[Path] = []
        for proj in self.home.iterdir():
            if proj.is_dir():
                out.extend(proj.glob("*.jsonl"))
        return sorted(out)

    def parse(self, path: Path) -> NormalisedConversation | None:
        legacy = _load_legacy()

        entries: list[dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return None
        if not entries:
            return None

        msg_entries = [e for e in entries if isinstance(e.get("message"), dict)]
        if not msg_entries:
            return None

        if _is_title_generator_session(msg_entries):
            return None

        # cwd from the JSONL is the authoritative project_path.
        real_cwd = legacy._first_cwd(entries)
        project_path = real_cwd
        if not project_path:
            # Fall back to the directory name (hyphen-mangled, see legacy code).
            project_path = path.parent.name.replace("-", "/") if path.parent.name else None

        first = msg_entries[0]
        session_id = first.get("sessionId")
        if not session_id:
            return None

        model = None
        for e in msg_entries:
            m = e.get("message", {})
            if m.get("role") == "assistant" and m.get("model"):
                model = m["model"]
                break

        entrypoint = first.get("entrypoint") or "claude-code"
        git_branch = first.get("gitBranch") or None

        started_at = legacy.parse_timestamp(first.get("timestamp"))
        ended_at = legacy.parse_timestamp(msg_entries[-1].get("timestamp"))
        tokens_in, tokens_out = legacy._sum_usage(msg_entries)

        norm: list[NormalisedMessage] = []
        for e in msg_entries:
            msg = e.get("message", {})
            role = legacy.map_role(e)
            content = legacy.extract_content(msg)
            tool_calls = legacy.extract_tool_calls(msg) or None
            tool_name = tool_calls[0].get("tool_name") if tool_calls else None
            norm.append(
                NormalisedMessage(
                    role=role,
                    content=content,
                    content_blocks=msg.get("content") if isinstance(msg.get("content"), list) else None,
                    tool_calls=tool_calls,
                    tool_name=tool_name,
                    created_at=legacy.parse_timestamp(e.get("timestamp")),
                    model=msg.get("model"),
                    token_count=legacy._per_message_total(msg),
                    is_sidechain=bool(e.get("isSidechain", False)),
                    parent_uuid=e.get("parentUuid"),
                    uuid=e.get("uuid"),
                )
            )

        return NormalisedConversation(
            session_id=session_id,
            project_path=project_path,
            model=model,
            entrypoint=entrypoint,
            git_branch=git_branch,
            started_at=started_at,
            ended_at=ended_at,
            messages=norm,
            token_count_in=tokens_in or None,
            token_count_out=tokens_out or None,
            metadata={"source": "claude_code"},
            source_tool="claude_code",
        )
