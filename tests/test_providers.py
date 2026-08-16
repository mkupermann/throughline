"""The provider registry is the single definition of provider identity."""

from __future__ import annotations

from throughline import providers as P
from throughline.adapters.registry import all_adapters


def test_registry_lists_exactly_the_nine_providers():
    assert P.NAMES == {
        "claude_code",
        "windsurf",
        "hermes",
        "codex",
        "continue",
        "cline",
        "vibe",
        "cursor",
        "zed",
    }


def test_registry_matches_the_installed_adapters():
    """A new adapter must not be able to exist without a provider entry.

    This is the assertion that keeps the registry honest as adapters are
    added — the failure mode it prevents is a provider that ingests data
    and then renders as 'unknown' forever.
    """
    assert {a.name for a in all_adapters()} == P.NAMES


def test_every_provider_has_a_distinct_label():
    labels = [p.label for p in P.PROVIDERS]
    assert len(set(labels)) == len(labels)
    assert P.by_name("claude_code").label == "Claude Code"


def test_unknown_and_none_render_as_unattributed():
    assert P.label_for(None) == "(unattributed)"
    assert P.label_for("no_such_tool") == "no_such_tool"


def test_by_name_returns_none_for_unknown():
    assert P.by_name("no_such_tool") is None
