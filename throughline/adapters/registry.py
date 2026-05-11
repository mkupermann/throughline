"""Adapter discovery — built-in + ``throughline.adapters`` entry point.

The CLI calls :func:`all_adapters` to learn which sources exist. Third
parties can publish a pip package with::

    [project.entry-points."throughline.adapters"]
    my_tool = "my_pkg.adapter:MyToolAdapter"

and Throughline picks it up at runtime without code changes here.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from typing import Iterator

from .base import Adapter

# Built-in adapter import paths. Listed (rather than auto-discovered) so
# import errors surface immediately and the load order is stable.
_BUILTIN_PATHS: tuple[str, ...] = (
    "throughline.adapters.claude_code:ClaudeCodeAdapter",
    "throughline.adapters.windsurf:WindsurfAdapter",
    "throughline.adapters.hermes:HermesAdapter",
    "throughline.adapters.codex:CodexAdapter",
    "throughline.adapters.continue_dev:ContinueDevAdapter",
)


def _load_path(path: str) -> type[Adapter]:
    module_name, _, cls_name = path.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, cls_name)


def _iter_entrypoint_adapters() -> Iterator[type[Adapter]]:
    try:
        eps = importlib.metadata.entry_points(group="throughline.adapters")
    except TypeError:  # py<3.10 fallback (we require 3.10+ anyway)
        eps = importlib.metadata.entry_points().get("throughline.adapters", [])
    for ep in eps:
        try:
            yield ep.load()
        except Exception:
            # Swallow third-party load failures; ingestion of other
            # sources should not be blocked by one broken adapter.
            continue


def all_adapters() -> list[Adapter]:
    """Return one instance of every registered adapter.

    Built-ins come first in their declared order, then any entry-point
    contributions. Duplicates by ``name`` are de-duped, built-ins
    winning.
    """
    seen: set[str] = set()
    out: list[Adapter] = []
    for path in _BUILTIN_PATHS:
        try:
            cls = _load_path(path)
        except ModuleNotFoundError:
            # Adapter module not bundled yet — let the rest still load.
            continue
        a = cls()
        if a.name in seen:
            continue
        out.append(a)
        seen.add(a.name)
    for cls in _iter_entrypoint_adapters():
        try:
            a = cls()
        except Exception:
            continue
        if a.name in seen:
            continue
        out.append(a)
        seen.add(a.name)
    return out


def get_adapter(name: str) -> Adapter | None:
    for a in all_adapters():
        if a.name == name:
            return a
    return None
