"""Shared persistence + idempotency layer for adapters.

The writer is the single piece of code that touches the ``conversations``,
``messages`` and ``ingestion_log`` tables. Each adapter only emits
``NormalisedConversation`` objects; the writer handles:

- Connection + friendly DB-down errors.
- File-hash idempotency: re-running an unchanged file is a no-op; a
  changed file deletes and re-inserts the conversation's messages so we
  never accumulate duplicates.
- Auto-backfill of the ``projects`` table at the end so the GUI's
  Projects page never sits empty after a successful first ingest.

Adapters call :func:`run_adapter` (one source) or :func:`run_many` (the
``--all`` path). Both return :class:`IngestSummary` objects ready to print.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_values

from throughline.message_derivations import lock_conversations
from throughline.self_referential import first_user_text, self_referential_reason

from .base import Adapter, IngestSummary, NormalisedConversation


def scrub_nul(value: Any) -> Any:
    """Remove U+0000 from anything on its way into a text or jsonb column.

    PostgreSQL text cannot hold a NUL byte, and psycopg2 refuses the whole
    statement rather than the one value: "A string literal cannot contain NUL
    (0x00) characters". The writer then rolls the session back, so one stray
    byte anywhere in a transcript costs every message in it — a 224-message
    Claude Code session ingested nowhere for exactly this reason.

    Dropping the byte is the only option that keeps the session: it carries no
    meaning in a transcript, and the alternative is losing the conversation.
    Recurses into the JSON payloads, where a NUL fails the same insert for the
    same reason.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: scrub_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_nul(v) for v in value]
    return value


def _db_config() -> dict[str, Any]:
    return {
        "dbname": os.environ.get("PGDATABASE", "throughline"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


def _connect() -> psycopg2.extensions.connection:
    cfg = _db_config()
    try:
        return psycopg2.connect(**cfg)
    except psycopg2.OperationalError as e:
        sys.stderr.write(
            f"ERROR: Cannot connect to PostgreSQL at "
            f"{cfg['host']}:{cfg['port']}/{cfg['dbname']}.\n"
            f"  Is it running? Try: docker compose up -d\n"
            f"  Or: brew services start postgresql@16\n"
            f"  Underlying error: {e}\n"
        )
        raise SystemExit(2) from e


def _upsert_conversation(cur: Any, conv: NormalisedConversation) -> int:
    cur.execute(
        """
        INSERT INTO conversations
            (session_id, project_path, model, entrypoint, git_branch,
             started_at, ended_at, message_count,
             token_count_in, token_count_out, summary, metadata, source_tool)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id) DO UPDATE
        SET project_path    = EXCLUDED.project_path,
            model           = EXCLUDED.model,
            entrypoint      = EXCLUDED.entrypoint,
            git_branch      = EXCLUDED.git_branch,
            started_at      = EXCLUDED.started_at,
            ended_at        = EXCLUDED.ended_at,
            message_count   = EXCLUDED.message_count,
            token_count_in  = EXCLUDED.token_count_in,
            token_count_out = EXCLUDED.token_count_out,
            summary         = EXCLUDED.summary,
            metadata        = EXCLUDED.metadata,
            source_tool     = EXCLUDED.source_tool,
            updated_at      = NOW()
        RETURNING id
        """,
        (
            conv.session_id,
            conv.project_path,
            conv.model,
            conv.entrypoint,
            conv.git_branch,
            conv.started_at,
            conv.ended_at,
            len(conv.messages),
            conv.token_count_in,
            conv.token_count_out,
            conv.summary,
            Json(conv.metadata or {}),
            conv.source_tool,
        ),
    )
    return cur.fetchone()[0]


#: Rows per INSERT statement. 500 keeps the generated statement well inside
#: any practical parameter limit while capturing nearly all of the win —
#: measured against loopback Postgres, batching is ~5x faster than one
#: execute() per message and the curve is flat well before this point:
#:
#:      messages     per-row     batched    speedup
#:           200      71.6ms      13.3ms       5.4x
#:         1,000     277.7ms      60.8ms       4.6x
#:         5,000    1486.4ms     224.4ms       6.6x
#:        20,000    5118.9ms     960.1ms       5.3x
#:
#: Both forms are linear in message count; this is a constant-factor
#: round-trip saving, not an algorithmic fix.
_MESSAGE_BATCH_SIZE = 500


def _replace_messages(cur: Any, conv_id: int, conv: NormalisedConversation) -> int:
    lock_conversations(cur, [conv_id])
    # Message IDs are replaced below. Remove rows keyed to those IDs first so
    # semantic search and graph views cannot retain orphaned derivations.
    cur.execute(
        """
        DELETE FROM embeddings
        WHERE source_type = 'message'
          AND source_id IN (SELECT id FROM messages WHERE conversation_id = %s)
        """,
        (conv_id,),
    )
    cur.execute(
        """
        DELETE FROM entity_mentions
        WHERE (source_type = 'message'
               AND source_id IN (SELECT id FROM messages WHERE conversation_id = %s))
           OR (source_type = 'conversation' AND source_id = %s)
        """,
        (conv_id, conv_id),
    )
    cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conv_id,))
    if not conv.messages:
        return 0

    rows = [
        (
            conv_id,
            m.uuid,
            m.parent_uuid,
            m.role,
            scrub_nul(m.content),
            Json(scrub_nul(m.content_blocks)) if m.content_blocks is not None else None,
            Json(scrub_nul(m.tool_calls)) if m.tool_calls else None,
            m.tool_name,
            m.is_sidechain,
            m.model,
            m.token_count,
            m.created_at or conv.started_at,
            Json(scrub_nul(m.metadata or {})),
        )
        for m in conv.messages
    ]

    execute_values(
        cur,
        """
        INSERT INTO messages
            (conversation_id, uuid, parent_uuid, role, content,
             content_blocks, tool_calls, tool_name, is_sidechain,
             model, token_count, created_at, metadata)
        VALUES %s
        """,
        rows,
        page_size=_MESSAGE_BATCH_SIZE,
    )
    return len(rows)


def _backfill_projects_from_observed(cur: Any) -> int:
    """Materialise rows in ``projects`` for every observed project_name.

    Idempotent. ON CONFLICT DO NOTHING so manually-edited rows are never
    clobbered. Returns the number of rows actually inserted.
    """
    cur.execute("""
        SELECT DISTINCT project_name
        FROM (
            SELECT project_name FROM memory_chunks WHERE project_name IS NOT NULL AND project_name <> ''
            UNION ALL
            SELECT project_name FROM conversations  WHERE project_name IS NOT NULL AND project_name <> ''
        ) o
        """)
    observed = [r[0] for r in cur.fetchall()]
    if not observed:
        return 0
    cur.execute("SELECT name FROM projects")
    existing = {r[0] for r in cur.fetchall()}
    missing = [n for n in observed if n not in existing]
    if not missing:
        return 0
    cur.executemany(
        "INSERT INTO projects (name, status) VALUES (%s, 'active') ON CONFLICT (name) DO NOTHING",
        [(n,) for n in missing],
    )
    return len(missing)


def _record_decision(conn: Any, cur: Any, fp: Path, fhash: str) -> None:
    """Log a file we deliberately declined to ingest, with ``record_count = 0``.

    A file that parses to nothing — an empty transcript, or one this tool
    recognises as its own ``claude -p`` call — used to be rolled back and
    skipped without leaving any trace. Three consequences, all bad:

    1. `pending` counts discovered files that have no ``ingestion_log`` row, so
       these stayed pending forever, pinning the provider chip to an amber
       "N pending" that no amount of ingesting could clear — and a warning that
       cannot be cleared is one the user learns to ignore, including on the day
       it means something real.
    2. Every subsequent run re-read and re-parsed every one of them.
    3. The decision was invisible: nothing recorded that the file was seen and
       judged, so "we never looked at it" and "we looked and said no" were
       indistinguishable after the fact.

    ``record_count = 0`` distinguishes a decline from a real ingest, and the
    ``(file_path, file_hash)`` key means a file that later grows gets a new hash
    and is judged again rather than being written off permanently.

    Rolls back first: the caller reaches here from an aborted parse, and on a
    failed transaction PostgreSQL rejects every further statement until the
    block ends.
    """
    conn.rollback()
    try:
        cur.execute(
            "INSERT INTO ingestion_log (file_path, file_hash, record_count) VALUES (%s, %s, 0) ON CONFLICT DO NOTHING",
            (str(fp), fhash),
        )
        conn.commit()
    except Exception:
        # Never let bookkeeping abort an ingest run: the next file matters more
        # than recording this one's rejection.
        conn.rollback()


def run_adapter(adapter: Adapter, *, conn: Any | None = None, verbose: bool = True) -> IngestSummary:
    """Run a single adapter against the DB.

    Returns an :class:`IngestSummary`. When ``conn`` is provided, the
    caller owns its lifecycle; otherwise one is opened and closed here.
    """
    summary = IngestSummary(adapter=adapter.name)
    if not adapter.is_present():
        if verbose:
            print(f"  [{adapter.name}] no data directory ({adapter.home}) — skipped.")
        return summary

    owns_conn = conn is None
    if conn is None:
        conn = _connect()
    cur = conn.cursor()

    try:
        files = list(adapter.discover())
        summary.files_seen = len(files)
        if verbose:
            print(f"  [{adapter.name}] found {len(files)} file(s)")

        for fp in files:
            try:
                fhash = adapter.sha256_file(fp)
            except OSError as e:
                summary.errors += 1
                if verbose:
                    print(f"    ✗ {fp.name}: {e}")
                continue

            cur.execute(
                "SELECT 1 FROM ingestion_log WHERE file_path=%s AND file_hash=%s",
                (str(fp), fhash),
            )
            if cur.fetchone():
                summary.skipped += 1
                continue

            cur.execute(
                "SELECT 1 FROM ingestion_log WHERE file_path=%s LIMIT 1",
                (str(fp),),
            )
            is_refresh = cur.fetchone() is not None

            try:
                parsed = adapter.parse(fp)
                # Normalise to a list so the code below handles both
                # one-conversation-per-file (Claude Code JSONL, Hermes
                # JSON, Codex rollout) and many-conversations-per-file
                # (Hermes state.db SQLite) uniformly.
                if parsed is None:
                    summary.skipped += 1
                    _record_decision(conn, cur, fp, fhash)
                    continue
                convs = parsed if isinstance(parsed, list) else [parsed]
                convs = [c for c in convs if c and c.messages]

                # Drop transcripts that are Throughline calling `claude -p`
                # itself. Claude Code records those calls as sessions, so
                # without this every ingest sweeps the tool's own prompts back
                # in as if they were the user's work — 81% of the author's
                # corpus at the time this was added. They also crowd the
                # extraction queue, which is ordered newest-first, and each one
                # costs a `claude -p` call to learn it holds nothing.
                kept = []
                for c in convs:
                    reason = self_referential_reason(first_user_text(c.messages))
                    if reason:
                        summary.self_referential += 1
                        if verbose:
                            print(f"    ~ {fp.name}: skipped ({reason} prompt)")
                    else:
                        kept.append(c)
                convs = kept

                if not convs:
                    summary.skipped += 1
                    _record_decision(conn, cur, fp, fhash)
                    continue
                written = 0
                for conv in convs:
                    conv_id = _upsert_conversation(cur, conv)
                    written += _replace_messages(cur, conv_id, conv)
                cur.execute(
                    "INSERT INTO ingestion_log (file_path, file_hash, record_count) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (str(fp), fhash, written),
                )
                conn.commit()
                summary.messages_written += written
                if is_refresh:
                    summary.refreshed += 1
                    if verbose:
                        print(f"    ↻ {fp.name}: {written} messages (refreshed)")
                else:
                    summary.ingested += 1
                    if verbose:
                        print(f"    ✓ {fp.name}: {written} messages")
            except Exception as e:
                conn.rollback()
                summary.errors += 1
                if verbose:
                    print(f"    ✗ {fp.name}: {e}")

        # Materialise projects table after each adapter run that wrote
        # at least one new session (skip when only refreshes happened —
        # that won't introduce new project_names).
        if summary.ingested > 0:
            try:
                inserted = _backfill_projects_from_observed(cur)
                conn.commit()
                if inserted and verbose:
                    print(f"    + materialised {inserted} new project(s)")
            except Exception as e:
                conn.rollback()
                if verbose:
                    print(f"    projects backfill skipped: {e.__class__.__name__}")
    finally:
        cur.close()
        if owns_conn:
            conn.close()

    return summary


def run_many(adapters: Iterable[Adapter], *, verbose: bool = True) -> list[IngestSummary]:
    """Run several adapters under one DB connection."""
    conn = _connect()
    out: list[IngestSummary] = []
    try:
        for a in adapters:
            out.append(run_adapter(a, conn=conn, verbose=verbose))
    finally:
        conn.close()
    return out
