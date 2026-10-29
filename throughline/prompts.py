"""What language the model should write memory in.

The extraction, titling and reflection prompts were written in German, because
the person who wrote them works in German. That made them unreadable to most of
the people the project is now published for, and — worse — it silently forced
German output onto every user regardless of what language their sessions were
in.

Translating the instructions is the easy half. The hard half is the output, and
the naive fix is wrong in both directions: hard-coding English would relabel a
German corpus one conversation at a time, and leaving German hard-coded does the
same to everyone else. Neither is a default anyone chose.

So the default is to follow the material. A German session yields German memory,
an English session yields English memory, and a person who works in both keeps
both — which is what was actually happening before, for one user, by accident.
``THROUGHLINE_MEMORY_LANG`` forces one language when a mixed corpus is the
problem rather than the point.

Note what this deliberately is *not*: a set of per-language prompt translations.
The instructions stay in English and the model is told to answer in the
material's language, which costs nothing per language added and works for any
language the chosen model handles. Honest limit: that has been exercised on
German and English. A model weak in your language will extract weakly in it,
and no wording here can fix that — pick a model that speaks it.
"""

from __future__ import annotations

import os

#: Set to a language name — "English", "Deutsch", "Français" — to force it.
_ENV = "THROUGHLINE_MEMORY_LANG"

_FOLLOW = (
    "Write your output in the same language and script as the material above. "
    "Do not translate and do not transliterate: a German session yields German "
    "memory, a Japanese session yields Japanese memory in Japanese script. "
    "Keep identifiers, code, and proper nouns exactly as they appear."
)


def output_language() -> str:
    """The language clause to paste into a prompt.

    Returned as a full sentence rather than a bare language name so the caller
    interpolates one ``{LANG}`` and cannot get the phrasing subtly wrong in one
    prompt out of six.
    """
    forced = os.environ.get(_ENV, "").strip()
    if forced:
        return f"Write your output in {forced}, whatever language the material above is in."
    return _FOLLOW
