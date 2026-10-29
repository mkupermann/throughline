"""The nine providers, defined once.

Provider identity was previously re-derived in three places — the API, the
UI, and ``conflicts.py`` — from ``conversations.entrypoint``, a column that
means different things depending on which adapter wrote it. That divergence
is the root cause in the design spec §1.1. This module is the single answer
to "which tools does Throughline unify?".

``chart_slot`` indexes the validated six-slot chart palette. Nine providers
against six hues is a real constraint, so slots repeat; provider chips carry
a text label as well, and the Timeline deliberately uses intensity rather
than hue (spec §5.2) so nothing depends on nine distinct colours existing.
"""

from __future__ import annotations

from dataclasses import dataclass

UNATTRIBUTED_LABEL = "(unattributed)"


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    chart_slot: int


PROVIDERS: tuple[Provider, ...] = (
    Provider("claude_code", "Claude Code", 1),
    Provider("windsurf", "Windsurf", 2),
    Provider("hermes", "Hermes", 3),
    Provider("codex", "Codex", 4),
    Provider("continue", "Continue", 5),
    Provider("cline", "Cline", 6),
    Provider("vibe", "Vibe", 1),
    Provider("cursor", "Cursor", 2),
    Provider("zed", "Zed", 3),
)

NAMES: frozenset[str] = frozenset(p.name for p in PROVIDERS)

_BY_NAME: dict[str, Provider] = {p.name: p for p in PROVIDERS}


def by_name(name: str) -> Provider | None:
    return _BY_NAME.get(name)


def label_for(name: str | None) -> str:
    """Display label. NULL is a state we render, not one we hide."""
    if name is None or name == "":
        return UNATTRIBUTED_LABEL
    p = _BY_NAME.get(name)
    return p.label if p else name
