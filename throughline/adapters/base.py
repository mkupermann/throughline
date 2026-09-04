"""Adapter ABC and the data classes the writer consumes.

The contract intentionally stays small. An adapter:

- says whether the tool's data directory exists on this machine
  (``is_present``) — cheap, no parsing;
- ``discover`` yields the files that will be ingested; ``discover_all``
  yields every candidate including excluded ones; ``excluded_reason``
  explains an exclusion;
- parses one file into a ``NormalisedConversation`` (``parse``).

Everything else — DB connections, idempotency via ``ingestion_log``,
project-bucket backfill, error handling — lives in the shared writer so
adapters stay tiny and individually testable.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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
    #: WHICH tool produced this conversation — the adapter's own ``name``.
    #: Distinct from ``entrypoint``, which is HOW that tool was invoked.
    #: Adapters must set this; leaving it None means "unattributed" and the
    #: UI will render it as such rather than guessing.
    source_tool: str | None = None


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
    #: Transcripts of Throughline's own `claude -p` calls, dropped at
    #: ingest. Counted separately from `skipped` so a run says plainly
    #: how much of a source is the tool's own exhaust.
    self_referential: int = 0


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

    @abstractmethod
    def discover(self) -> Iterable[Path]:
        """Yield the files ingestion will process. May be empty.

        This must ALREADY exclude anything unsafe to ingest. An adapter that
        widens its search must widen ``discover_all`` and narrow here — see
        ``excluded_reason`` and the design spec §9.1.
        """

    def discover_all(self) -> Iterable[Path]:
        """Every candidate file, including ones excluded from ingestion.

        The *coverage* view: what exists on disk, for counting and reporting.
        Defaults to ``discover()``, so an adapter with no exclusions needs no
        extra code — and a third-party adapter published through the
        ``throughline.adapters`` entry point keeps working untouched.
        """
        return self.discover()

    def excluded_reason(self, path: Path) -> str | None:
        """Why *path* is discovered but must not be ingested, or None.

        Default: nothing is excluded.
        """
        return None

    def is_present(self) -> bool:
        """Does this tool have any data on this box?

        Was "the directory exists", which reported `cline` as present while
        it contributed nothing. Now: at least one candidate file was found.
        """
        home = self.home.expanduser()
        if not home.exists():
            return False
        return any(True for _ in self.discover_all())

    @abstractmethod
    def parse(self, path: Path) -> NormalisedConversation | list[NormalisedConversation] | None:
        """Parse a single discovered file.

        Returns:
          - a single ``NormalisedConversation`` (the common case: one
            file = one conversation, e.g. Claude Code JSONL).
          - a ``list[NormalisedConversation]`` when one source file
            carries multiple conversations (e.g. a SQLite ``state.db``
            with N session rows). The writer will upsert each
            independently and use a per-conversation row replace for
            messages, so growing files don't accumulate duplicates.
          - ``None`` to skip the file entirely.
        """

    # Helpers shared by most adapters.

    @staticmethod
    def sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        if path.is_dir():
            # Directory-based sources (e.g. Vibe session dirs): hash every
            # file's relative path + content in sorted order so the digest
            # changes iff the session content changes.
            for fp in sorted(p for p in path.rglob("*") if p.is_file()):
                h.update(str(fp.relative_to(path)).encode())
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        h.update(chunk)
            return h.hexdigest()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def declined_ingestion_fingerprint(self, content_hash: str) -> str:
        """Key for a parser-specific decision not to ingest a file.

        Most adapters use the content hash directly. An adapter can override
        this when a parser upgrade must reconsider only files an older parser
        declined, while leaving successful imports idempotent.
        """
        return content_hash
