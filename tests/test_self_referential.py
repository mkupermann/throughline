"""Throughline's own `claude -p` prompts must not be ingested as user work.

Four scripts shell out to the Claude CLI, and Claude Code records each call as a
session transcript. Without a filter, the next ingest sweeps those back in. On
the author's corpus that was 2,783 of 3,423 conversations — 81% — and they also
crowd the extraction queue, which is ordered newest-first.

The risk runs both ways, so both directions are tested: a missed prompt wastes
an extraction slot, but a false positive silently discards real memory, which is
the far more expensive mistake.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from throughline.self_referential import first_user_text, self_referential_reason


# The exact openings recorded on disk, including the older wordings. Each entry
# is (opening text, which script it came from).
TOOL_PROMPTS = [
    ("Du bekommst einen Auszug aus einer Claude Code Session. Generiere einen "
     "prägnanten deutschen Titel (max 8 Wörter).", "generate_titles"),
    ("Du analysierst eine Entwickler-Session (Claude Code, Codex, Hermes, Continue, "
     "Windsurf, Cline) und extrahierst Memory-Chunks.", "extract_memory"),
    # The earlier wording — 642 transcripts still open this way.
    ("Du analysierst eine Claude Code Entwickler-Session und extrahierst verwertbare "
     "Erkenntnisse als JSON.", "extract_memory"),
    ("Du analysierst ein Session-Transcript und extrahierst strukturierte Entitäten "
     "+ Beziehungen als JSON.", "extract_entities"),
    ("Du bekommst zwei Memory-Chunks aus einer persoenlichen Wissensdatenbank. "
     "Chunk A (ID 22, erstellt 2026-01-01)", "reflect_memory"),
    ("Du bekommst zwei Memory-Chunks die denselben Sachverhalt beschreiben. "
     "Formuliere einen einzigen Chunk.", "reflect_memory"),
]

# Real work that must survive. The mail-analyst prompt is deliberately included:
# it is machine-generated too, but it belongs to a different tool, and widening
# the filter to catch it would discard memory Throughline is meant to keep.
REAL_WORK = [
    "Ich brauche ein Mac App, die die Music.app crates anzeigen kann.",
    # Stands in for a machine-generated prompt belonging to a *different*
    # tool. Paraphrased rather than quoted: the original was a real prompt
    # from the author's own private work, and a test fixture is a poor place
    # to keep somebody's correspondence. What the case tests is the shape —
    # "You are X's assistant for Y" — not the person in it.
    "Du bist der Mail-Analyst von A. B. A. ist externer Berater.",
    "# Datumsformat Automatisieren - Text-zu-Datum Konvertierung",
    "Why does the timeline show only one page of results?",
    "",
]


@pytest.mark.parametrize("text,script", TOOL_PROMPTS, ids=[s for _, s in TOOL_PROMPTS])
def test_tool_prompts_are_recognised(text, script):
    assert self_referential_reason(text) == script


@pytest.mark.parametrize("text", REAL_WORK, ids=lambda t: (t[:28] or "empty"))
def test_real_work_is_not_flagged(text):
    assert self_referential_reason(text) is None


def test_none_is_handled():
    assert self_referential_reason(None) is None


def test_matching_is_anchored_not_substring():
    """A conversation that DISCUSSES the extractor must not be mistaken for it.

    This is the failure mode that would quietly delete real memory: debugging
    sessions about the prompt quote the prompt.
    """
    quoting = (
        "The extractor is broken. Its prompt starts with "
        '"Du analysierst eine Entwickler-Session" and it returns nothing.'
    )
    assert self_referential_reason(quoting) is None


def test_leading_whitespace_and_case_do_not_defeat_it():
    assert self_referential_reason(
        "\n\n   DU BEKOMMST EINEN AUSZUG AUS EINER CLAUDE CODE SESSION. Generiere..."
    ) == "generate_titles"


# ── first_user_text ──────────────────────────────────────────────────────────


@dataclass
class _Msg:
    role: str
    content: str


def test_first_user_text_skips_non_user_roles():
    msgs = [_Msg("system", "you are a helper"), _Msg("assistant", "hi"), _Msg("user", "real question")]
    assert first_user_text(msgs) == "real question"


def test_first_user_text_accepts_dicts():
    assert first_user_text([{"role": "user", "content": "from a dict"}]) == "from a dict"


def test_first_user_text_returns_none_when_no_user_message():
    assert first_user_text([_Msg("assistant", "only me")]) is None
    assert first_user_text([]) is None
    assert first_user_text(None) is None


def test_end_to_end_a_generated_transcript_is_dropped():
    """The shape the writer actually sees."""
    msgs = [
        _Msg("user", "Du bekommst einen Auszug aus einer Claude Code Session. Generiere einen Titel."),
        _Msg("assistant", "Provider-Sichtbarkeit wiederhergestellt"),
    ]
    assert self_referential_reason(first_user_text(msgs)) == "generate_titles"
