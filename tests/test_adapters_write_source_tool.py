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


# --------------------------------------------------------------------------- #
# Bytes PostgreSQL cannot store                                               #
# --------------------------------------------------------------------------- #


def test_a_nul_byte_does_not_cost_the_whole_session():
    """PostgreSQL text columns cannot hold U+0000.

    A single NUL anywhere in a transcript made psycopg2 refuse the insert with
    "A string literal cannot contain NUL (0x00) characters", the writer rolled
    the session back, and the whole conversation was dropped — not a damaged
    message, no message at all. Found on a real Claude Code session of 224
    messages that ingested nowhere.
    """
    from throughline.adapters.writer import scrub_nul

    assert scrub_nul("vorher\x00nachher") == "vorhernachher"
    assert scrub_nul("sauber") == "sauber"
    assert scrub_nul(None) is None
    assert scrub_nul("") == ""


def test_nested_structures_are_scrubbed_too():
    # Tool calls and content blocks are JSON, and a NUL inside one of those
    # fails the same insert for the same reason.
    from throughline.adapters.writer import scrub_nul

    payload = {"cmd": "echo\x00", "args": ["a\x00b", {"deep": "c\x00"}], "n": 3, "ok": True}
    assert scrub_nul(payload) == {"cmd": "echo", "args": ["ab", {"deep": "c"}], "n": 3, "ok": True}


def test_scrubbing_leaves_everything_else_alone():
    from throughline.adapters.writer import scrub_nul

    text = "Zeilen\numbruch\ttab ümlaut \U0001f600 emoji"
    assert scrub_nul(text) == text
