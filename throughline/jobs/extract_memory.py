#!/usr/bin/env python3
"""
Memory extraction pipeline.

The model comes from ``throughline.llm``, the same probe the answer feature
uses: Ollama first, then any OpenAI-compatible server named in
``THROUGHLINE_ANSWER_BASE_URL``, then the ``claude`` CLI, then hosted OpenAI.
A machine running a local model extracts without a network call and without
having been configured to avoid one.

This was the last pipeline tied to one vendor. It shelled out to ``claude -p``
unconditionally, so the feature that turns transcripts into durable memory —
the thing this product is for — required one specific vendor's CLI inside a
product whose whole claim is that it does not. Two independent reviews named
it as the gap between "would use" and "would adopt", which is a fair reading:
a memory layer you cannot fill without vendor X is vendor X's memory layer.

The prompt itself is English; what language it *answers* in comes from
``throughline.prompts.output_language``, which follows the transcript unless
``THROUGHLINE_MEMORY_LANG`` overrides it. Rewording anything here means adding
the new opening line to ``throughline.self_referential._MARKERS`` — otherwise
the tool stops recognising its own calls and re-ingests them as the user's
work. ``tests/test_self_referential.py`` fails if you forget.

By default the transcript is run through ``throughline.pii.redact`` before it
is sent — set ``THROUGHLINE_REDACT_PII=0`` to disable.
"""

import json
import os
import sys
import time
from typing import Any

import psycopg2

from throughline import llm as _llm
from throughline import prompts as _prompts
from throughline.pii import count_redactions, redact
from throughline.self_referential import agent_call_cwd

DB_CONFIG: dict[str, Any] = {
    "dbname": os.environ.get("PGDATABASE", "throughline"),
    "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", "5432")),
}

REDACT_PII: bool = os.environ.get("THROUGHLINE_REDACT_PII", "1") != "0"


def _connect() -> "psycopg2.extensions.connection":
    """Connect to PostgreSQL with a friendly error if the DB is unreachable."""
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}.\n"
            f"  Is it running? Try: docker compose up -d\n"
            f"  Or: brew services start postgresql@16\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e


def _require_model() -> str:
    """Confirm some model can be reached, or say which three ways out exist.

    `throughline.llm` already composes that message — it knows which backends
    it probed and why each was rejected. Reproducing the check here would give
    the user a second, worse explanation of the same failure.
    """
    info = _llm.backend_info()
    if not info.available:
        sys.stderr.write(f"ERROR: no model available for extraction.\n  {info.detail}\n")
        raise SystemExit(2)
    return str(info)


#: Model override for extraction only. Empty means "whatever the probe found",
#: which is the right default: the machine's configured model is a property of
#: the machine, not of this script.
MODEL = os.environ.get("THROUGHLINE_EXTRACT_MODEL", "").strip() or None
MAX_CONVERSATIONS_PER_RUN = 20
MIN_MESSAGES = 5
MAX_TRANSCRIPT_CHARS = 80000
# Cap per-message content shown to the extractor. The previous 1,000-char
# cap silently beheaded any long assistant message — multi-axis plans,
# ranked recommendation lists, deep reviews. Anything above ~6 KB used to
# disappear behind a truncation marker. The transcript-level
# MAX_TRANSCRIPT_CHARS cap (80,000) already protects against runaway
# prompt growth, so the per-message cap only needs to be wide enough that
# a single richly-structured assistant turn survives intact.
MAX_MESSAGE_CHARS = 8000
# Chunks per conversation. A 300-message session that includes a multi-axis
# plan (e.g. 4 axes × 5 bullets, plus an 8-PR sequence) needs more than 10
# slots if the structure is to be preserved instead of collapsed into a
# generic "project_context" blurb.
MAX_CHUNKS_PER_CONVERSATION = 25
SLEEP_BETWEEN_CALLS = 2.0
# Raised from 120 → 300 s. With MAX_MESSAGE_CHARS at 8,000 the transcript
# can carry richer multi-paragraph assistant turns, which gives the model
# more to read and more to emit (now up to 25 chunks vs 10). The previous
# 2-minute cap was tight for 200+ message sessions; observed timeouts on
# conv #10 (297 msgs) and #46 (210 msgs) zeroed their chunks because the
# clear-before-reextract is committed even when the LLM call gives up.
TIMEOUT_PER_CALL = 300

PROMPT_TEMPLATE = """You are reading one developer session from an AI coding assistant (Claude Code, Codex, Cursor, Zed, Vibe, Hermes, Continue, Cline, Windsurf) and extracting what is worth keeping, as structured JSON.

Extract ONLY non-obvious things that will be useful in a FUTURE session:
- decision: an architectural choice and its reason ("pgvector over Milvus, because...")
- pattern: a reusable technique ("AppleScript is faster with a whose-filter")
- insight: something surprising that was learned ("KeyVault RBAC blocks app access")
- preference: how this person wants to work ("prefers terse answers, no filler")
- contact: a person, their role, their context ("Jane Doe = migration lead, Project Alpha")
- error_solution: a problem and what actually fixed it ("pg16 + pgvector: compile it yourself")
- project_context: durable knowledge about a project ("Project Alpha ships Q2/2026")
- workflow: how something is run ("launchd job: install-schedule.sh install")

IMPORTANT — preserve structure, do not summarise it away:
If the session contains a structured plan, a ranked list of recommendations, or a
multi-axis framework (for example "Axis A/B/C/D", "Tier 1/2/3", "PR 1/8, PR 2/8, ...",
a numbered roadmap, a review with scores), then emit ONE CHUNK PER RANKED ITEM or
PER AXIS — do not collapse the whole thing into a single "insight" line. Keep the
original order, keep any time or effort estimate ("~2 hr"), keep the reasoning in
one or two sentences, and tag every chunk of the same framework alike (for example
tags=["throughline-review","axis-A","stunning"]) so they can be reassembled later.

{LANG}

Output: a PURE JSON array — no markdown fences, no explanation — with at most {MAX_CHUNKS} chunks.

Format:
[
  {"content": "...", "category": "decision", "tags": ["postgresql", "pgvector"], "confidence": 0.9, "project": "throughline"}
]

If there is nothing worth keeping: []

Transcript:

{TRANSCRIPT}

Return ONLY the JSON array, nothing else."""


def build_transcript(messages: list[tuple[str, str | None]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m[0]
        content = m[1] or ""
        if role == "tool_result":
            continue
        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "...[truncated]"
        parts.append(f"[{role.upper()}]\n{content}\n")
    transcript = "\n".join(parts)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
    return transcript


def parse_json_response(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}")
        return []


def call_model(prompt: str) -> str:
    """Send the prompt to whichever backend the probe found. Never raises.

    An empty string means "this conversation yielded nothing" and the caller
    already handles that, so a failed call degrades to skipping one
    conversation rather than aborting a 20-conversation run.
    """
    text, err = _llm.complete(
        prompt,
        timeout=TIMEOUT_PER_CALL,
        model=MODEL,
        # Only the claude CLI cares: Claude Code names the project folder after
        # the process CWD, so inheriting the repo's would file this call inside
        # the user's real project history, and the next ingest would read it
        # back as their work. See throughline.self_referential.
        cwd=str(agent_call_cwd()),
    )
    if text is None:
        print(f"    extraction call failed: {err}")
        return ""
    return text


#: Kept as an alias: this function had one name for a year and it appears in
#: other people's scripts and in the tests.
call_claude = call_model


def extract_for_conversation(cursor: Any, conv_id: int) -> int:
    cursor.execute(
        """
        SELECT role::text, content
        FROM messages
        WHERE conversation_id = %s AND role IN ('user', 'assistant')
        ORDER BY created_at
    """,
        (conv_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return 0

    transcript = build_transcript(rows)
    if len(transcript) < 200:
        return 0

    if REDACT_PII:
        redacted = redact(transcript)
        n = count_redactions(transcript, redacted)
        if n:
            print(f"    redacted {n} secret/PII match(es) before extraction")
        transcript = redacted

    prompt = (
        PROMPT_TEMPLATE.replace("{MAX_CHUNKS}", str(MAX_CHUNKS_PER_CONVERSATION))
        .replace("{LANG}", _prompts.output_language())
        .replace("{TRANSCRIPT}", transcript)
    )
    response = call_model(prompt)
    if not response:
        return 0

    chunks = parse_json_response(response)
    # Cap defensively in case the model ignores the prompt — extra chunks are
    # truncated rather than rejected, so we never silently drop a session.
    if len(chunks) > MAX_CHUNKS_PER_CONVERSATION:
        chunks = chunks[:MAX_CHUNKS_PER_CONVERSATION]
    inserted = 0
    for chunk in chunks:
        try:
            content = chunk.get("content", "").strip()
            category = chunk.get("category", "insight")
            tags = chunk.get("tags", [])
            confidence = float(chunk.get("confidence", 0.8))
            project = chunk.get("project") or None
            if not content or category not in [
                "decision",
                "pattern",
                "insight",
                "preference",
                "contact",
                "error_solution",
                "project_context",
                "workflow",
            ]:
                continue
            cursor.execute(
                """
                INSERT INTO memory_chunks (source_type, source_id, content, category, tags, confidence, project_name)
                VALUES ('conversation', %s, %s, %s, %s, %s, %s)
            """,
                (conv_id, content, category, tags, confidence, project),
            )
            inserted += 1
        except Exception as e:
            print(f"    Insert-Fehler: {e}")
            continue

    return inserted


def _parse_id_list(raw: str) -> list[int]:
    """Parse a comma-separated id list like '10,15,47' into [10,15,47]."""
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError as exc:
            raise SystemExit(f"--force-conversations: '{part}' is not an integer") from exc
    return out


def _force_reextract(cursor, conv_ids: list[int]) -> tuple[int, int, int]:
    """Delete previous chunks for the given conversations and re-extract.

    Returns ``(deleted, inserted, errors)``. Each conversation is processed
    in its own transaction at the caller's commit() boundary, so a failure
    on one ID doesn't roll back the others.
    """
    deleted_total = 0
    inserted_total = 0
    errors = 0
    for cid in conv_ids:
        cursor.execute(
            "DELETE FROM memory_chunks WHERE source_type='conversation' AND source_id=%s RETURNING id",
            (cid,),
        )
        deleted = len(cursor.fetchall())
        deleted_total += deleted
        print(f"  #{cid} re-extract (cleared {deleted} old chunk(s))", end=" ", flush=True)
        try:
            n = extract_for_conversation(cursor, cid)
            inserted_total += n
            print(f"→ {n} Chunks")
        except Exception as e:
            errors += 1
            print(f"✗ {e}")
            raise
    return deleted_total, inserted_total, errors


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Extract memory chunks from ingested conversations.")
    parser.add_argument(
        "--force-conversations",
        metavar="IDS",
        help="Comma-separated conversation IDs to re-extract. Deletes their "
        "existing memory_chunks and runs extraction again with the "
        "current prompt and limits. Use this after changing the "
        "extractor to refresh affected rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_CONVERSATIONS_PER_RUN,
        metavar="N",
        help=f"How many conversations to process this run. Default: "
        f"{MAX_CONVERSATIONS_PER_RUN}. Each one costs a separate `claude -p` "
        f"call, so this is the cost dial — raise it deliberately.",
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="Only conversations started on or after DATE (YYYY-MM-DD). "
        "Selection is newest-first, so this narrows a large backlog to a "
        "period you actually care about instead of walking it blindly.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be extracted and stop. Makes no `claude -p` calls "
        "and writes nothing — use it to see the size of a run before paying "
        "for it.",
    )
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be at least 1")

    print("=" * 60)
    print("Throughline — memory extraction")
    print("=" * 60)

    print(f"Model: {_require_model()}")
    conn = _connect()
    cursor = conn.cursor()

    if args.force_conversations:
        conv_ids = _parse_id_list(args.force_conversations)
        print(f"\nForce-re-extracting {len(conv_ids)} conversation(s): {conv_ids}\n")
        try:
            deleted, inserted, errors = _force_reextract(cursor, conv_ids)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"\n{'=' * 60}")
        print(
            f"Re-extracted: {len(conv_ids)} | Old chunks cleared: {deleted} | New chunks: {inserted} | Errors: {errors}"
        )
        print(f"{'=' * 60}")
        cursor.close()
        conn.close()
        return

    # `--limit` and `--since` are user input, so they are bound as parameters
    # rather than interpolated. MIN_MESSAGES stays inline: it is a module
    # constant and never request-derived.
    cursor.execute(
        f"""
        SELECT c.id, c.project_name, c.message_count
        FROM conversations c
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_chunks mc
            WHERE mc.source_type = 'conversation' AND mc.source_id = c.id
        )
        AND c.message_count >= {MIN_MESSAGES}
        AND (%(since)s IS NULL OR c.started_at >= %(since)s::date)
        ORDER BY c.started_at DESC
        LIMIT %(limit)s
        """,
        {"since": args.since, "limit": args.limit},
    )
    convs = cursor.fetchall()

    # How much is left behind, so a run never implies it drained the queue.
    cursor.execute(
        f"""
        SELECT count(*) FROM conversations c
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_chunks mc
            WHERE mc.source_type = 'conversation' AND mc.source_id = c.id
        )
        AND c.message_count >= {MIN_MESSAGES}
        AND (%(since)s IS NULL OR c.started_at >= %(since)s::date)
        """,
        {"since": args.since},
    )
    pending = cursor.fetchone()[0]

    scope = f" seit {args.since}" if args.since else ""
    print(f"\n{len(convs)} von {pending} offenen Conversations{scope} (limit={args.limit})\n")
    if not convs:
        print("Nichts zu tun.")
        return

    if args.dry_run:
        for cid, proj, n in convs:
            print(f"  #{cid} ({proj or '–'}, {n} Msgs)")
        remaining = pending - len(convs)
        print(f"\nDry run — nichts extrahiert, nichts geschrieben. {remaining} would still be pending afterwards.")
        cursor.close()
        conn.close()
        return

    total_chunks = 0
    errors = 0

    for conv_id, project_name, msg_count in convs:
        print(f"  #{conv_id} ({project_name or '–'}, {msg_count} Msgs)", end=" ", flush=True)
        try:
            n = extract_for_conversation(cursor, conv_id)
            conn.commit()
            total_chunks += n
            print(f"→ {n} Chunks")
        except Exception as e:
            conn.rollback()
            errors += 1
            print(f"✗ {e}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    print(f"\n{'=' * 60}")
    print(f"Analysiert: {len(convs)} | Chunks: {total_chunks} | Fehler: {errors}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
