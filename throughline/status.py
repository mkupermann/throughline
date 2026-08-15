"""Health / inventory snapshot for a Throughline DB.

One source of truth for three surfaces:

  * ``throughline status`` CLI subcommand (human + ``--json``)
  * ``memory.stats`` MCP tool
  * the web UI (Overview verdict, Operate panel)

Each surface formats the same dict; nobody re-implements the SQL.

Design notes
------------

The function never raises on a missing/unreachable DB. It returns a dict
with ``db_reachable=False`` and an ``error`` key, so the GUI and the MCP
tool can render a useful message instead of crashing. Tests rely on this
behaviour to validate the JSON shape without standing up Postgres.

Schema-version reporting is best-effort: if a ``schema_migrations`` table
does not exist the field is set to ``None``, not an error — the project
ships ``sql/schema.sql`` as the authoritative schema and not every install
has Alembic-style migrations applied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Schema-derived constants — kept in lock-step with sql/schema.sql.
# Listed explicitly (not auto-discovered) so a missing table is loud.
_CORE_TABLES: tuple[str, ...] = (
    "conversations",
    "messages",
    "memory_chunks",
    "embeddings",
    "memory_reflections",
    "skills",
    "prompts",
    "projects",
    "entities",
    "entity_mentions",
    "relationships",
)

# Categories declared in the memory_chunks ``category`` enum (sql/schema.sql).
_CATEGORIES: tuple[str, ...] = (
    "decision",
    "pattern",
    "insight",
    "preference",
    "contact",
    "error_solution",
    "project_context",
    "workflow",
)

# ``memory_chunks.source_type`` values that mean "memory was derived from
# something", as opposed to reorganised from memory that already existed.
# 'reflection_merge' and 'consolidation' are deliberately absent: the reflection
# job runs on its own schedule and would keep this timestamp looking fresh while
# extraction had stalled for weeks.
#
# The column is plain ``text`` with no CHECK constraint, so a value that never
# occurs produces no error anywhere — it silently matches nothing. Keep this
# tuple in step with what actually writes chunks: scripts/extract_memory.py
# ('conversation'), memory_mcp/server.py ('mcp_write'), and the manual path
# ('manual'). test_status_extraction_source_types_exist guards the drift.
_EXTRACTION_SOURCE_TYPES: tuple[str, ...] = (
    "conversation",
    "mcp_write",
    "manual",
)


@dataclass
class StatusPayload:
    """Typed wrapper around the snapshot dict.

    The dict form is what every surface (CLI/MCP/GUI) actually consumes —
    this dataclass exists so test code can construct fixtures without
    duplicating field names.
    """

    db_reachable: bool
    captured_at: str
    schema_version: str | None = None
    #: None = could not be determined (no tracking table, or no source tree
    #: to compare against); [] = checked, nothing pending. Never conflate.
    pending_migrations: list[str] | None = None
    error: str | None = None
    table_row_counts: dict[str, int] = field(default_factory=dict)
    chunks_total: int = 0
    chunks_by_category: dict[str, int] = field(default_factory=dict)
    embedding_coverage_pct: float = 0.0
    last_extraction_at: str | None = None
    last_reflection_at: str | None = None
    contradictions_outstanding: int = 0
    projects_count: int = 0
    # Drift-audit summary fields (populated from the most-recent
    # memory_reflections row with reflection_type='audit').
    last_audit_at: str | None = None
    last_audit_sampled: int = 0
    last_audit_drifted: int = 0
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_reachable": self.db_reachable,
            "captured_at": self.captured_at,
            "schema_version": self.schema_version,
            "pending_migrations": self.pending_migrations,
            "error": self.error,
            "table_row_counts": dict(self.table_row_counts),
            "chunks_total": self.chunks_total,
            "chunks_by_category": dict(self.chunks_by_category),
            "embedding_coverage_pct": self.embedding_coverage_pct,
            "last_extraction_at": self.last_extraction_at,
            "last_reflection_at": self.last_reflection_at,
            "contradictions_outstanding": self.contradictions_outstanding,
            "projects_count": self.projects_count,
            "last_audit_at": self.last_audit_at,
            "last_audit_sampled": self.last_audit_sampled,
            "last_audit_drifted": self.last_audit_drifted,
            "version": self.version,
        }


def _empty_payload(*, error: str | None = None) -> dict[str, Any]:
    from throughline import __version__

    return StatusPayload(
        db_reachable=False,
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        error=error,
        table_row_counts={t: 0 for t in _CORE_TABLES},
        chunks_by_category={c: 0 for c in _CATEGORIES},
        version=__version__,
    ).to_dict()


def _connect():
    """Open a DB connection using libpq env vars. Returns None on failure.

    Lives here (not imported from memory_mcp.db) so this module stays usable
    in environments where the optional ``mcp`` SDK isn't installed.
    """
    try:
        import psycopg2  # type: ignore
    except Exception:
        return None
    cfg = {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "throughline"),
        "user": os.environ.get("PGUSER", os.environ.get("USER") or "postgres"),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", "3")),
    }
    pw = os.environ.get("PGPASSWORD")
    if pw:
        cfg["password"] = pw
    try:
        conn = psycopg2.connect(**cfg)
        # Autocommit: a swallowed query error must not leave the transaction
        # aborted and poison every subsequent (read-only) query.
        conn.autocommit = True
        return conn
    except Exception:
        return None


def _safe_count(cur, table: str) -> int:
    try:
        cur.execute(f"SELECT count(*) FROM public.{table}")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _parse_drift_count(reasoning: str | None, action: str | None) -> int:
    """Extract the integer drift count from an audit-row reasoning string.

    The auditor writes reasoning of the form
    ``"Sampled N chunks, mean recall X, threshold Y, M drifted."`` —
    pull M out via regex. Falls back to 0 when the format is missing
    OR when ``action_taken='no_drift_detected'``, which is the
    canonical zero-drift state.
    """
    import re

    if action == "no_drift_detected":
        return 0
    if not reasoning:
        return 0
    m = re.search(r"(\d+)\s+drift(?:ed)?\b", reasoning)
    return int(m.group(1)) if m else 0


def _schema_version(cur) -> str | None:
    """The most recently applied migration, or None if nothing tracks them.

    Reads ``applied_migrations`` — the table ``scripts/migrate.py`` actually
    creates and writes (see sql/migrations/README.md). This asked for a
    ``schema_migrations`` table with a ``version`` column until 2026-08-10.
    No such table has ever existed, so a fully migrated database reported
    "(no schema_migrations table)", which reads as *migrations are not tracked
    here* — the opposite of the truth, and it hid a migration that had been
    pending long enough to matter (001_message_dedup, whose absence let a
    still-growing session lose its new messages).

    Ordered by name, not ``applied_at``: a database migrated in one run gets
    identical timestamps for every row, so ordering by time returns an
    arbitrary member of that set.
    """
    try:
        cur.execute("SELECT to_regclass('public.applied_migrations') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        cur.execute(
            "SELECT migration_name FROM public.applied_migrations "
            "ORDER BY migration_name DESC LIMIT 1"
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


def _pending_migrations(cur) -> list[str] | None:
    """Migration files on disk that ``applied_migrations`` does not record.

    Returns None when the answer is unknowable — for example, when no tracking
    table exists. None means "not checked"; an empty list means "checked,
    nothing pending". The caller must not collapse the two, or a status report
    that could not look would claim all-clear.
    """
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    if not migrations_dir.is_dir():
        return None
    try:
        cur.execute("SELECT to_regclass('public.applied_migrations') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        cur.execute("SELECT migration_name FROM public.applied_migrations")
        applied = {str(r[0]) for r in cur.fetchall()}
    except Exception:
        return None
    return sorted(p.name for p in migrations_dir.glob("*.sql") if p.name not in applied)


def collect_status(*, conn=None) -> dict[str, Any]:
    """Return a JSON-safe dict describing the DB.

    If ``conn`` is supplied it is used as-is and not closed (tests pass a
    mock). If ``conn`` is None we open one ourselves with ``_connect`` and
    close it on the way out.
    """
    from throughline import __version__

    owns_conn = False
    if conn is None:
        conn = _connect()
        owns_conn = True
        if conn is None:
            return _empty_payload(error="DB unreachable (psycopg2 missing or connection refused)")

    try:
        with conn.cursor() as cur:
            payload = StatusPayload(
                db_reachable=True,
                captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                version=__version__,
                table_row_counts={t: _safe_count(cur, t) for t in _CORE_TABLES},
                chunks_by_category={c: 0 for c in _CATEGORIES},
            )
            payload.schema_version = _schema_version(cur)
            payload.pending_migrations = _pending_migrations(cur)
            payload.chunks_total = payload.table_row_counts.get("memory_chunks", 0)

            try:
                cur.execute(
                    "SELECT category::text, count(*) FROM public.memory_chunks "
                    "WHERE status = 'active' GROUP BY category"
                )
                for cat, n in cur.fetchall():
                    payload.chunks_by_category[str(cat)] = int(n)
            except Exception:
                pass

            try:
                cur.execute(
                    """
                    SELECT
                        (SELECT count(*) FROM public.memory_chunks) AS chunks,
                        (SELECT count(*) FROM public.embeddings
                            WHERE source_type = 'memory_chunk') AS embedded
                    """
                )
                row = cur.fetchone()
                if row and row[0]:
                    chunks, embedded = int(row[0]), int(row[1] or 0)
                    payload.embedding_coverage_pct = round(100.0 * embedded / chunks, 2)
            except Exception:
                pass

            # Chunks the extractor and the MCP write tool produced — i.e. memory
            # that was *derived*, as opposed to merged or consolidated by the
            # reflection job, which would otherwise make extraction look fresh
            # while it had in fact stalled.
            #
            # This asked for source_type IN ('extraction', 'mcp_write') until
            # 2026-08-10. Neither value has ever existed: the extractor writes
            # 'conversation' and the manual path writes 'manual'. max() over an
            # empty set is NULL, not an error, so the indicator read "—" on a
            # database with 986 chunks — indistinguishable from "nothing
            # extracted yet". The column is plain text with no constraint, so
            # nothing rejected the typo. See _EXTRACTION_SOURCE_TYPES.
            try:
                cur.execute(
                    "SELECT max(created_at) FROM public.memory_chunks "
                    "WHERE source_type = ANY(%s)",
                    (list(_EXTRACTION_SOURCE_TYPES),),
                )
                row = cur.fetchone()
                if row and row[0]:
                    payload.last_extraction_at = row[0].isoformat()
            except Exception:
                pass

            try:
                cur.execute("SELECT max(created_at) FROM public.memory_reflections")
                row = cur.fetchone()
                if row and row[0]:
                    payload.last_reflection_at = row[0].isoformat()
            except Exception:
                pass

            try:
                cur.execute(
                    "SELECT count(*) FROM public.memory_reflections "
                    "WHERE reflection_type = 'contradiction' "
                    "AND (action_taken IS NULL OR action_taken = 'flagged')"
                )
                row = cur.fetchone()
                payload.contradictions_outstanding = int(row[0]) if row else 0
            except Exception:
                pass

            try:
                cur.execute(
                    "SELECT count(DISTINCT project_name) FROM public.memory_chunks " "WHERE project_name IS NOT NULL"
                )
                row = cur.fetchone()
                payload.projects_count = int(row[0]) if row else 0
            except Exception:
                pass

            # Drift audit — most recent row with reflection_type='audit'.
            # ``affected_chunks`` is the sampled-id list, length = sampled.
            # ``action_taken`` distinguishes drift-flagged runs from clean ones.
            # ``reasoning`` carries the parseable counts ("… N drifted.").
            try:
                cur.execute(
                    """
                    SELECT created_at, action_taken,
                           COALESCE(array_length(affected_chunks, 1), 0) AS sampled,
                           reasoning
                    FROM public.memory_reflections
                    WHERE reflection_type = 'audit'
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row:
                    when, action, sampled, reasoning = row
                    payload.last_audit_at = when.isoformat() if when else None
                    payload.last_audit_sampled = int(sampled or 0)
                    # Parse the drift count out of the reasoning string the
                    # auditor writes; falls back to 0 if the format ever drifts.
                    payload.last_audit_drifted = _parse_drift_count(reasoning, action)
            except Exception:
                pass

            return payload.to_dict()
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:
                pass


def format_human(payload: dict[str, Any]) -> str:
    """Pretty-print a status payload for the CLI."""
    lines: list[str] = []
    head = "Throughline status"
    if payload.get("version"):
        head += f" — v{payload['version']}"
    lines.append(head)
    lines.append("=" * len(head))
    lines.append(f"Captured: {payload.get('captured_at', '?')}")
    if not payload.get("db_reachable"):
        lines.append("DB:       unreachable")
        if payload.get("error"):
            lines.append(f"Error:    {payload['error']}")
        return "\n".join(lines)

    lines.append("DB:       reachable")
    sv = payload.get("schema_version")
    lines.append(f"Schema:   {sv if sv else '(migrations not tracked)'}")
    # A pending migration is the one status line worth interrupting for: the
    # database is the only copy of most of this history, and a schema the code
    # does not expect is how that copy gets damaged. `None` means the check
    # could not run, which is not the same as "nothing pending" and must not
    # print as silence.
    pending = payload.get("pending_migrations")
    if pending:
        lines.append(f"Pending:  {len(pending)} migration(s) — {', '.join(pending)}")
        lines.append("          run: python3 scripts/migrate.py")
    elif pending is None:
        lines.append("Pending:  (not checked — no migrations directory beside the package)")
    lines.append("")
    lines.append("Table row counts:")
    for t, n in (payload.get("table_row_counts") or {}).items():
        lines.append(f"  {t:<22} {n:>10,}")
    lines.append("")
    lines.append(f"Memory chunks total:        {payload.get('chunks_total', 0):,}")
    lines.append(f"Embedding coverage:         {payload.get('embedding_coverage_pct', 0.0):.2f}%")
    lines.append(f"Projects:                   {payload.get('projects_count', 0)}")
    lines.append(f"Last extraction at:         {payload.get('last_extraction_at') or '—'}")
    lines.append(f"Last reflection at:         {payload.get('last_reflection_at') or '—'}")
    lines.append(f"Contradictions outstanding: {payload.get('contradictions_outstanding', 0)}")
    lines.append("")
    lines.append("Chunks by category:")
    for cat, n in (payload.get("chunks_by_category") or {}).items():
        lines.append(f"  {cat:<22} {n:>10,}")
    return "\n".join(lines)
