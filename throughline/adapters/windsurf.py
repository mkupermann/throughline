"""Windsurf plans (~/.windsurf/plans/) adapter.

Windsurf plans are single Markdown files; we materialise each as a
one-message conversation. Idempotency is by file content hash, same as
the other adapters.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .base import Adapter, NormalisedConversation, NormalisedMessage


def _extract_title(content: str, filename: str) -> str:
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if m:
        return m.group(1).strip()[:200]
    stem = Path(filename).stem
    stem = re.sub(r"-[a-f0-9]{6}$", "", stem)
    return stem.replace("-", " ").replace("_", " ").title()[:200]


class WindsurfAdapter(Adapter):
    name = "windsurf"
    label = "Windsurf plans"
    home = Path("~/.windsurf/plans").expanduser()

    def discover(self) -> Iterable[Path]:
        if not self.home.exists():
            return []
        files: list[Path] = []
        files.extend(self.home.glob("*.md"))
        files.extend(self.home.glob("*.txt"))
        return sorted(files)

    def parse(self, path: Path) -> NormalisedConversation | None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        if len(content) < 50:
            return None
        title = _extract_title(content, path.name)
        stat = path.stat()
        ctime = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path)))
        return NormalisedConversation(
            session_id=session_id,
            project_path="windsurf",
            model="windsurf-cascade",
            entrypoint="windsurf",
            source_tool="windsurf",
            started_at=ctime,
            ended_at=mtime,
            summary=title,
            messages=[
                NormalisedMessage(
                    role="user",
                    content=content,
                    created_at=ctime,
                    metadata={"source_file": str(path)},
                )
            ],
            metadata={
                "source": "windsurf",
                "file": path.name,
                "kind": "plan",
            },
        )
