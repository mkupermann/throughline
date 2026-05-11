"""Adapter ABC and the data classes the writer consumes.

The contract intentionally stays small. An adapter:

- says whether the tool's data directory exists on this machine
  (``is_present``) — cheap, no parsing;
- yields the files that look like conversations (``discover``);
- parses one file into a ``NormalisedConversation`` (``parse``).

Everything else — DB connections, idempotency via ``ingestion_log``,
project-bucket backfill, error handling — lives in the shared writer so
adapters stay tiny and individually testable.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


# Roles understood by the messages.role enum in the DB schema.
MessageRole = str  # one of: "user", "assistant", "system", "tool_result"


@dataclass
class NormalisedMessage:
    """One row's worth of ``messages`` table data, source-agnostic.

    ``role`` must be one of the values in the DB enum. ``content`` is the
    rendered plain text shown in the GUI. ``content_blocks`` retains the
    original structured payload (when the source used one) so nothing is
    lost. ``metadata`` is a free-form JSON envelope for source-specific
    fields the writer should round-trip into ``messages.metadata``.
    """

    role: MessageRole
    content: str
    content_blocks: Any | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_name: str | None = None
    created_at: datetime | None = None
    model: str | None = None
    token_count: int | None = None
    is_sidechain: bool = False
    parent_uuid: str | None = None
    uuid: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalisedConversation:
    """One row's worth of ``conversations`` table data.

    ``session_id`` must be a UUID string. Adapters whose source ids aren't
    UUIDs should derive one deterministically (``uuid.uuid5``) so
    re-ingestion stays idempotent.

    ``project_path`` is taken literally — ``conversations.project_name``
    is a generated column (the last path segment), so to bucket your
    source under a single project (e.g. "hermes") just set
    ``project_path="hermes"``.
    """

    session_id: str
    project_path: str | None
    model: str | None
    entrypoint: str | None
    started_at: datetime
    ended_at: datetime | None
    messages: list[NormalisedMessage]
    git_branch: str | None = None
    token_count_in: int | None = None
    token_count_out: int | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestSummary:
    """Per-adapter result of a run, suitable for printing to a CLI."""

    adapter: str
    files_seen: int = 0
    ingested: int = 0
    refreshed: int = 0
    skipped: int = 0
    errors: int = 0
    messages_written: int = 0


class Adapter(ABC):
    """Subclass this in ``throughline/adapters/<name>.py``.

    Required attributes:

    - ``name`` — short, kebab-or-snake identifier used on the CLI
      (``--source <name>``) and inside ``IngestSummary``. Must be unique
      across all adapters loaded into the process.
    - ``label`` — human-readable name for help text and listings.
    - ``home`` — the directory the adapter scans. Used both by
      ``is_present`` (fast detection) and by listings ("Hermes Agent
      (~/.hermes/sessions/)"). May be a *template* path (``~/...``) — the
      adapter resolves it with ``Path.expanduser`` internally.
    """

    name: str
    label: str
    home: Path

    def is_present(self) -> bool:
        """Cheap detection — does this tool's data dir exist on this box?"""
        return self.home.expanduser().exists()

    @abstractmethod
    def discover(self) -> Iterable[Path]:
        """Yield candidate conversation files. May be empty."""

    @abstractmethod
    def parse(self, path: Path) -> NormalisedConversation | None:
        """Parse a single discovered file. Return None if it should be skipped."""

    # Helpers shared by most adapters.

    @staticmethod
    def sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
