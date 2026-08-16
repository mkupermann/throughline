"""Table-driven over all nine adapters: each writes its own name.

Written table-driven rather than as nine separate tests so that a tenth
adapter cannot be added without this failing — the omission this guards
against is exactly how `vibe` shipped writing an empty string and `hermes`
shipped trusting a payload field.
"""

from __future__ import annotations

import inspect

import pytest

from throughline import providers as P
from throughline.adapters.base import NormalisedConversation
from throughline.adapters.registry import all_adapters


def test_normalised_conversation_carries_source_tool():
    assert "source_tool" in inspect.signature(NormalisedConversation).parameters


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda a: a.name)
def test_adapter_sets_source_tool_to_its_own_name(adapter):
    """Bar 3: no adapter may leave provider identity to chance."""
    src = inspect.getsource(type(adapter))
    assert (
        "source_tool=" in src
    ), f"{adapter.name} never sets source_tool; provider identity would be NULL for everything it ingests"
    assert (
        f'source_tool="{adapter.name}"' in src or "source_tool=self.name" in src
    ), f"{adapter.name} must write its own registered name"


@pytest.mark.parametrize("adapter", all_adapters(), ids=lambda a: a.name)
def test_adapter_name_is_a_registered_provider(adapter):
    assert adapter.name in P.NAMES


def test_entrypoint_is_left_alone():
    """Spec §8: entrypoint semantics do not change."""
    from throughline.adapters import claude_code, vibe

    assert 'entrypoint=""' in inspect.getsource(vibe.VibeAdapter)
    assert "entrypoint=entrypoint" in inspect.getsource(claude_code.ClaudeCodeAdapter)
