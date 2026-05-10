"""Health / inventory snapshot for a Throughline DB.

One source of truth for three surfaces:

  * ``throughline status`` CLI subcommand (human + ``--json``)
  * ``memory.stats`` MCP tool
  * Streamlit GUI Memory Health card

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
    error: str | None = None
    table_row_counts: dict[str, int] = field(default_factory=dict)
    chunks_total: int = 0
    chunks_by_category: dict[str, int] = field(default_factory=dict)
    embedding_coverage_pct: float = 0.0
    last_extraction_at: str | None = None
    last_reflection_at: str | None = None
    contradictions_outstanding: int = 0
    projects_count: int = 0
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_reachable": self.db_reachable,
            "captured_at": self.captured_at,
            "schema_version": self.schema_version,
            "error": self.error,
            "table_row_counts": dict(self.table_row_counts),
            "chunks_total": self.chunks_total,
            "chunks_by_category": dict(self.chunks_by_category),
            "embedding_coverage_pct": self.embedding_coverage_pct,
            "last_extraction_at": self.last_extraction_at,
            "last_reflection_at": self.last_reflection_at,
            "contradictions_outstanding": self.contradictions_outstanding,
            "projects_count": self.projects_count,
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
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": os.environ.get("PGUSER", os.environ.get("USER") or "postgres"),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", "3")),
    }
    pw = os.environ.get("PGPASSWORD")
    if pw:
        cfg["password"] = pw
    try:
        return psycopg2.connect(**cfg)
    except Exception:
        return None


def _safe_count(cur, table: str) -> int:
    try:
        cur.execute(f"SELECT count(*) FROM public.{table}")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _schema_version(cur) -> str | None:
    """Best-effort schema version. Returns None if no migrations table."""
    try:
        cur.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        cur.execute("SELECT version FROM public.schema_migrations " "ORDER BY applied_at DESC NULLS LAST LIMIT 1")
        row = cur.fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


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

            try:
                cur.execute(
                    "SELECT max(created_at) FROM public.memory_chunks "
                    "WHERE source_type = 'extraction' OR source_type = 'mcp_write'"
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
    lines.append(f"Schema:   {sv if sv else '(no schema_migrations table)'}")
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
