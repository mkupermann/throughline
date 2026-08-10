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
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, execute_values

from .base import Adapter, IngestSummary, NormalisedConversation


def _db_config() -> dict[str, Any]:
    return {
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


def _connect() -> "psycopg2.extensions.connection":
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
        SET ended_at      = EXCLUDED.ended_at,
            message_count = EXCLUDED.message_count,
            model         = COALESCE(EXCLUDED.model, conversations.model),
            metadata      = conversations.metadata || EXCLUDED.metadata,
            source_tool   = COALESCE(EXCLUDED.source_tool, conversations.source_tool),
            updated_at    = NOW()
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
    cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conv_id,))
    if not conv.messages:
        return 0

    rows = [
        (
            conv_id,
            m.uuid,
            m.parent_uuid,
            m.role,
            m.content,
            Json(m.content_blocks) if m.content_blocks is not None else None,
            Json(m.tool_calls) if m.tool_calls else None,
            m.tool_name,
            m.is_sidechain,
            m.model,
            m.token_count,
            m.created_at or conv.started_at,
            Json(m.metadata or {}),
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
    cur.execute(
        """
        SELECT DISTINCT project_name
        FROM (
            SELECT project_name FROM memory_chunks WHERE project_name IS NOT NULL AND project_name <> ''
            UNION ALL
            SELECT project_name FROM conversations  WHERE project_name IS NOT NULL AND project_name <> ''
        ) o
        """
    )
    observed = [r[0] for r in cur.fetchall()]
    if not observed:
        return 0
    cur.execute("SELECT name FROM projects")
    existing = {r[0] for r in cur.fetchall()}
    missing = [n for n in observed if n not in existing]
    if not missing:
        return 0
    cur.executemany(
        "INSERT INTO projects (name, status) VALUES (%s, 'active') "
        "ON CONFLICT (name) DO NOTHING",
        [(n,) for n in missing],
    )
    return len(missing)


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
                    conn.rollback()
                    continue
                convs = parsed if isinstance(parsed, list) else [parsed]
                convs = [c for c in convs if c and c.messages]
                if not convs:
                    summary.skipped += 1
                    conn.rollback()
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
