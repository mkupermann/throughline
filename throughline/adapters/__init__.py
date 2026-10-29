"""Source adapters for Throughline's ingest pipeline.

Each adapter knows how to discover and parse conversations from a specific
local AI tool (Claude Code, Hermes, Codex, Continue.dev, …). The shared
writer in ``writer.py`` then upserts those conversations into the
``conversations`` / ``messages`` tables and keeps ``ingestion_log``
honest.

Add a new tool in three steps:

1. Subclass ``Adapter`` in a new module (e.g. ``cursor.py``).
2. Implement ``is_present()``, ``discover()`` and ``parse(path)``.
3. Register it in ``registry.BUILTIN_ADAPTERS``.

Third-party packages can register their own adapters via the
``throughline.adapters`` setuptools entry point; ``registry.all_adapters()``
discovers both built-ins and entry-point-registered classes.
"""

from .base import (  # noqa: F401
    Adapter,
    IngestSummary,
    NormalisedConversation,
    NormalisedMessage,
)
from .registry import all_adapters, get_adapter  # noqa: F401
