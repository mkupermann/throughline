"""Claude Code (~/.claude/projects/<slug>/*.jsonl) adapter.

The existing ``scripts/ingest_sessions.py`` is the source of truth for
the parsing details (role mapping, content-block extraction, token
aggregation). This adapter re-uses those helpers so we don't fork
behaviour.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .base import Adapter, NormalisedConversation, NormalisedMessage
from throughline.self_referential import is_agent_call_transcript

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
    """Return the packaged session-ingestion helpers."""
    from throughline.jobs import ingest_sessions

    return ingest_sessions


class ClaudeCodeAdapter(Adapter):
    name = "claude_code"
    label = "Claude Code"
    home = Path("~/.claude/projects").expanduser()

    #: Subagent transcripts live at
    #: ``~/.claude/projects/<proj>/<session>/subagents/agent-*.jsonl`` and
    #: inherit their parent's ``sessionId``. See ``excluded_reason``.
    SUBAGENT_DIR = "subagents"

    def discover_all(self) -> Iterable[Path]:
        """Every transcript, at any depth.

        Was ``proj.glob("*.jsonl")`` — non-recursive, which could not reach
        133 of the 260 files present. The deeper ones are subagent
        transcripts; nobody decided to exclude them, the glob simply did not
        reach them.
        """
        if not self.home.exists():
            return []
        out: list[Path] = []
        for proj in self.home.iterdir():
            if proj.is_dir():
                out.extend(proj.rglob("*.jsonl"))
        return sorted(out)

    def excluded_reason(self, path: Path) -> str | None:
        """Subagent transcripts are counted, not ingested.

        A subagent's transcript carries its *parent's* ``sessionId``. The
        writer upserts ``ON CONFLICT (session_id)`` and replaces messages with
        a DELETE, so ingesting 33 subagent files plus the parent would resolve
        them all to one row, each deleting the previous one's messages — and
        report success. Ingesting them properly needs its own identity
        (uuid5 of parent + filename) and a `parent_session_id` column; that is
        specified as follow-up work in the design spec §9.3.

        Matches ``subagents`` anywhere in the path below ``home``, NOT just as
        the immediate parent. On this machine 26 of the 132 deeper files live
        at ``<session>/subagents/workflows/wf_<id>/agent-*.jsonl`` — their
        immediate parent is the workflow directory, and a
        ``path.parent.name == "subagents"`` test lets every one of them
        through to the writer. 25 of those 26 share a ``sessionId`` with a
        top-level file, so that narrower test would ship exactly the data loss
        this exclusion exists to prevent.

        The literal-parts check above can be defeated by a symlink: its own
        path may carry no ``subagents`` segment while it points into a real
        ``subagents/`` directory (a symlinked file inside a project dir, or a
        symlinked directory with a non-``subagents`` name whose target is
        ``subagents/``). ``rglob`` on Python < 3.13 follows directory
        symlinks by default (this only changed in 3.13's ``recurse_symlinks``
        default), and a symlinked *file* reaches ``discover()`` on every
        Python version regardless — its own path never contains
        ``subagents`` no matter the interpreter. So we re-run the same test
        against the fully resolved real path. Any failure to resolve — a
        broken symlink (target doesn't exist), a permission error, or a
        symlink loop — must not raise out of this method, and we exclude
        rather than ingest: over-excluding costs one file's worth of
        coverage, under-excluding reproduces exactly the sessionId collision
        this method exists to prevent.
        """
        try:
            rel = path.relative_to(self.home)
        except ValueError:
            rel = path
        if self.SUBAGENT_DIR in rel.parts:
            return "subagent transcript"

        # Transcripts Claude Code wrote for Throughline's own `claude -p` calls.
        # Checked by directory, which is a fact about where the call ran, rather
        # than by prompt wording, which is a guess about text — and one that has
        # already been wrong: the first version of the wording list missed 642
        # transcripts written under an earlier phrasing. The wording check still
        # runs later in the writer, because transcripts recorded before this
        # existed were written from the old working directory and can only be
        # recognised by what they say.
        if is_agent_call_transcript(path):
            return "throughline agent call"

        try:
            real_path = path.resolve(strict=True)
            real_home = self.home.resolve(strict=True)
            real_rel = real_path.relative_to(real_home)
        except (OSError, ValueError, RuntimeError):
            return "subagent transcript"
        if self.SUBAGENT_DIR in real_rel.parts:
            return "subagent transcript"
        return None

    def discover(self) -> Iterable[Path]:
        return [p for p in self.discover_all() if self.excluded_reason(p) is None]

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
