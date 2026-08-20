"""Recognise conversations that are Throughline talking to itself.

Four of Throughline's own scripts shell out to ``claude -p``: ``reflect_memory``,
``extract_entities``, ``extract_memory`` and ``generate_titles``. Claude Code
records each of those calls as a session transcript on disk, so the next
``ingest`` sweeps them back in as if they were work the user did.

On the author's machine that was 459 of 3,423 conversations — 13% of the corpus
— and it compounds: every extraction run walks the queue newest-first, and these
sit at the front because they are generated constantly. They yield nothing (the
extractor correctly finds no memory in a prompt asking it to find memory), but
they consume a slot and a ``claude -p`` call each time.

The markers below are the literal opening lines of those prompts. They live here
rather than being imported from the scripts because they must match what is
already written in transcripts on disk — text from a *past* run, which no longer
tracks the current source. If you change one of those prompts, add the new
opening here; the old one must stay, or previously-recorded sessions stop being
recognised.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Working directory for every ``claude -p`` call Throughline makes on its own
#: behalf.
#:
#: Claude Code files a transcript under a project directory derived from the
#: process's CWD (``/`` becomes ``-``), so a sub-call inherited from the repo
#: checkout lands in the *user's own* project history, indistinguishable by
#: location from work they did themselves. Giving those calls a directory of
#: their own moves them into a project slug nothing else writes to, which the
#: Claude Code adapter can then exclude structurally — the same mechanism that
#: already excludes subagent transcripts.
#:
#: This is what the ``_MARKERS`` list below cannot be: exact. Matching prompt
#: wording is a guess about text, and it has already been wrong once — the first
#: version of that list missed 642 transcripts written under an earlier
#: phrasing. A directory is a fact.
#:
#: The markers stay regardless: transcripts already on disk were written from
#: the old CWD and can only ever be recognised by their text.
_AGENT_CALL_DIRNAME = "agent-calls"


def _agent_call_path() -> Path:
    """Where the calls run, as a value. Touches nothing.

    Split from :func:`agent_call_cwd` because the exclusion predicate below runs
    once per discovered file, and a predicate that creates a directory as a side
    effect of answering a question is both wasteful and wrong — it also raises on
    a path the process may not write to, turning a read-only classification into
    an I/O error.
    """
    override = os.environ.get("THROUGHLINE_AGENT_CALL_DIR")
    if override:
        return Path(override)
    return Path.home() / ".throughline" / _AGENT_CALL_DIRNAME


def agent_call_cwd() -> Path:
    """The same directory, created if absent — for handing to ``subprocess``.

    Honours ``THROUGHLINE_AGENT_CALL_DIR`` so a test, or a machine where ``~``
    is not writable, can point it elsewhere.
    """
    path = _agent_call_path()
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_agent_call_transcript(path: Path) -> bool:
    """Is *path* a transcript Claude Code wrote for one of our own sub-calls?

    Compares the transcript's parent directory name to the slug Claude Code
    derives from :func:`agent_call_cwd`. Slugging is reproduced rather than
    imported because it is Claude Code's rule, not ours: every ``/`` in the
    absolute path becomes ``-``, and so does the ``.`` of a dotted directory,
    which is why ``~/.throughline`` yields a doubled dash.

    The comparison ignores the home directory the path starts with. Matching
    the whole absolute slug meant a container reading transcripts the host had
    written computed ``-home-throughline--throughline-agent-calls`` and
    compared it against ``-Users-alice--throughline-agent-calls``: no
    match, so Throughline's own model calls were filed as the user's work —
    fourteen of them on the corpus where this was found. Every second machine
    reproduces that, and it is the tail of the slug that identifies the
    directory anyway. An explicit ``THROUGHLINE_AGENT_CALL_DIR`` is still
    matched in full: it names one specific place, not a convention.
    """
    slug = str(_agent_call_path()).replace("/", "-").replace(".", "-")
    if path.parent.name == slug:
        return True
    if os.environ.get("THROUGHLINE_AGENT_CALL_DIR"):
        return False
    suffix = f"--throughline-{_AGENT_CALL_DIRNAME}"
    return path.parent.name.endswith(suffix)


#: (marker, which script produced it). Matched against the start of a
#: conversation's first user message, case-insensitively.
#:
#: Some scripts have been reworded over time and BOTH spellings appear on disk.
#: `extract_memory` is the clear case: 642 transcripts open with the older
#: "eine Claude Code Entwickler-Session" and 136 with the current
#: "eine Entwickler-Session (Claude Code, Codex, …)". Dropping the old one would
#: silently leave 642 conversations unrecognised, which is how this list came to
#: be short in the first place — the first version of it matched only the
#: current wording and missed two thirds of what it was written to catch.
_MARKERS: tuple[tuple[str, str], ...] = (
    # reflect_memory — contradiction check and merge prompts
    ("du bekommst zwei memory-chunks", "reflect_memory"),
    # extract_entities
    ("du analysierst ein session-transcript und extrahierst strukturierte", "extract_entities"),
    # extract_memory — current wording, then the earlier one
    ("du analysierst eine entwickler-session", "extract_memory"),
    ("du analysierst eine claude code entwickler-session", "extract_memory"),
    # generate_titles — by far the most numerous, one per untitled conversation
    ("du bekommst einen auszug aus einer claude code session", "generate_titles"),
    ("be concise. markdown format. same language as user.", "generate_titles"),
    # reflect_memory — three further wordings found in the corpus on
    # 2026-08-11, all present in numbers and none matched by the openings
    # above. Every one of them was written by an earlier version of the same
    # script; a marker list only ever describes the wordings someone has
    # looked for.
    ("zwei memory-chunks aus einer persoenlichen wissensdatenbank", "reflect_memory"),
    ("zwei memory-chunks aus einer persönlichen wissensdatenbank", "reflect_memory"),
    ("ein memory-chunk aus einer persoenlichen wissensdatenbank", "reflect_memory"),
    ("ein memory-chunk aus einer persönlichen wissensdatenbank", "reflect_memory"),
    # The eval harnesses. `run_eval` has shelled out to a model since April and
    # `retrieval_eval` since 2026-08-11; between them they had put 109
    # two-message conversations into the corpus they exist to measure.
    ("below is one record from someone's working history", "retrieval_eval"),
    ("you are answering a factual question about the throughline", "run_eval"),
    # English rewrites of the four prompts above, from 2026-08-12. The German
    # openings are kept — they are the only thing that identifies the years of
    # transcripts already on disk, and a marker list is append-only for exactly
    # this reason. Deleting a superseded wording does not tidy the list; it
    # un-labels history.
    ("you are reading one developer session from an ai coding assistant", "extract_memory"),
    ("you are reading a session transcript and extracting the entities", "extract_entities"),
    ("below is an excerpt from a session with an ai coding assistant", "generate_titles"),
    ("two memory chunks from one person's knowledge base", "reflect_memory"),
    ("two memory chunks describing the same thing", "reflect_memory"),
    ("one memory chunk from a person's knowledge base", "reflect_memory"),
    ("several memory chunks about the same subject", "reflect_memory"),
)

#: Marker an assistant puts at the head of a prompt it sends to another
#: assistant on the user's behalf.
#:
#: The durable answer to a problem text-matching cannot solve. Consultations
#: between agents arrive through the Vibe adapter looking exactly like the
#: user's own Vibe sessions — because that is what they are; the only
#: difference is who typed them, and no transcript records that. Matching the
#: phrasing an assistant habitually uses is guesswork that needs extending
#: every time the wording changes, which is precisely the failure mode this
#: module already suffered once with 642 missed transcripts.
#:
#: A tag is exact. Any agent scripting a call to another model should prepend
#: it; anything carrying it is labelled `agent-consultation` and kept out of
#: the user's own history. Costs six characters at the top of a prompt.
AGENT_BRIEF_TAG = "[agent-brief]"

#: Automation that is NOT Throughline's — the user's own scheduled tools,
#: which shell out to an assistant and are recorded as sessions the same way.
#:
#: Kept separate on purpose. These are the user's work in a sense Throughline's
#: own chatter never is: they ran because the user set them running, and the
#: memory in them may be worth keeping. But 153 identical invocations of one
#: skill are not a "session" anyone wants to read either, so they are labelled
#: rather than judged — the interface can offer to fold them away and the
#: person whose history it is decides.
#: Matched anywhere in the opening window rather than only at its start, and
#: written without the account holder's name — a marker keyed to one person
#: would be both useless to anyone else and a personal detail hardcoded into a
#: public repository.
_AUTOMATION_MARKERS: tuple[tuple[str, str], ...] = (
    ("/daily-mail-drafter", "daily-mail-drafter"),
    ("mail-analyst", "mail-analyst"),
    # One assistant consulting another on the user's behalf. These arrive
    # through the Vibe adapter looking exactly like the user's own Vibe
    # sessions, because that is what they are — the difference is who typed
    # them, and the transcript does not record that.
    #
    # Openly best-effort, and the weakest classification here: it matches the
    # framing an agent uses when it hands a colleague a brief. If the user
    # genuinely opens a session with "Runder Tisch", it is mislabelled — which
    # is why nothing is deleted and the label can be cleared by re-running the
    # backfill after editing this list. The durable fix is the same one used
    # for Claude Code: give those calls their own working directory so they
    # are separable by location rather than by wording.
    (AGENT_BRIEF_TAG.lower(), "agent-consultation"),
    # One-off list for consultations recorded before the tag existed. Kept
    # short and dated rather than grown: matching an assistant's habitual
    # phrasing is guesswork, and every entry here is a wording someone had to
    # notice first. New calls carry the tag above and need none of this.
    ("runder tisch", "agent-consultation"),
    ("du besetzt drei", "agent-consultation"),
    ("du bist senior product", "agent-consultation"),
    ("du bist senior ux", "agent-consultation"),
    ("du bist principal product designer", "agent-consultation"),
)

#: How far into the first message to look. The markers are opening lines, so a
#: short window is enough and stops a conversation that merely *quotes* one of
#: these prompts (a debugging session about the extractor, say) from being
#: mistaken for the prompt itself.
_WINDOW = 400


def self_referential_reason(first_user_message: str | None) -> str | None:
    """Return the script that generated this conversation, or None.

    Deliberately conservative: it matches only near the very start of the first
    user message. Over-excluding costs real memory; under-excluding costs a
    wasted extraction slot. The cheaper mistake is to let one through.
    """
    if not first_user_message:
        return None
    head = first_user_message[:_WINDOW].lstrip().lower()
    for marker, script in _MARKERS:
        if head.startswith(marker):
            return script
    return None


def generated_by(first_user_message: str | None) -> str | None:
    """What produced this conversation, or None if a person typed it.

    Broader than :func:`self_referential_reason`, and used for a different
    purpose. That function decides what never enters the database; this one
    labels what is already in it, including automation that is the user's own
    rather than Throughline's.

    The distinction is not pedantry. Measured on the author's corpus on
    2026-08-11: of 3,606 conversations, about 3,017 were Throughline's own
    `claude -p` calls and a further 247 were the user's scheduled tools — so
    roughly 340 were sessions a person actually had. Every list in the product
    was showing the other 90%, which is why "I see no coherent sessions, just
    two-message fragments" was the correct reading of the interface and not a
    misunderstanding of it.

    Nothing is deleted on the strength of this. It is a label, so a view can
    fold the machinery away and still let the reader open it.
    """
    if not first_user_message:
        return None
    reason = self_referential_reason(first_user_message)
    if reason:
        return reason
    head = first_user_message[:_WINDOW].lower()
    for marker, tool in _AUTOMATION_MARKERS:
        if marker in head:
            return tool
    return None


#: Labels produced by Throughline itself, as opposed to the user's own
#: automation. Kept as data so a caller can tell the two apart without
#: hardcoding the list a second time.
THROUGHLINE_GENERATORS = frozenset(script for _, script in _MARKERS)


def first_user_text(messages) -> str | None:
    """The first user-authored message's text, or None.

    Takes anything with ``.role`` and ``.content`` so it works on both
    ``NormalisedMessage`` at ingest time and a DB row at audit time.
    """
    for m in messages or ():
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        if role == "user":
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content")
            return content
    return None
