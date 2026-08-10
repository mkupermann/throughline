"""Cross-tool conflict detection — Throughline's signature analysis.

The premise: when the same project is touched by multiple AI CLIs
(Claude Code, Codex, Hermes, Continue, Cline, Windsurf) over time, the
tools sometimes record decisions or recommendations that contradict each
other. Per-tool memory can never surface this because each tool only sees
its own transcripts. Throughline has all of them in one schema, which
means it can.

This module finds three classes of cross-tool conflict:

1. **Documented supersession across tools.** A memory chunk from tool A
   was explicitly superseded (via the reflection pass, the MCP
   ``memory.supersede`` tool, or manual edit) by a chunk from tool B.
   That's a recorded change of mind whose provenance crosses tool
   boundaries. We don't generate these; we surface what's already there.

2. **Semantic near-duplicates with opposite sentiment.** Two memory
   chunks for the same project, same category (decision / pattern /
   insight), originating from different tools, with high cosine
   similarity on their embeddings — but the textual content shows
   contradiction markers ("instead", "no longer", "switched from",
   "actually", "rejected", "deprecated", "rolled back"). High-precision
   heuristic; tunable threshold.

3. **Stale-tool drift.** A chunk from tool A is older than N days, has
   never been accessed, and a newer chunk from tool B in the same
   project + category exists. Surfaces the case where one tool's
   "memory" is silently stale because you switched tools and never went
   back. Not technically a contradiction, but worth seeing.

All three return ``Conflict`` records with a stable schema so the CLI
and the Curate surface can consume them.

Public entry point: ``find_conflicts(conn=None, *, project=None,
since_days=None, min_similarity=0.85) -> ConflictReport``.

Conservatively read-only. Never writes to the DB. Never calls an LLM —
this is pure SQL + small Python heuristics. Cheap to run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ConflictChunk:
    """One side of a conflict — a single memory chunk with its tool provenance."""

    chunk_id: int
    tool: str                   # the conversations.entrypoint value (claude_code, codex, ...)
    project: str | None
    category: str
    content: str                # truncated to ~500 chars by the SQL
    created_at: str             # ISO timestamp
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Conflict:
    """One detected conflict — always two sides, plus the detection method."""

    kind: str                   # "supersession" | "semantic" | "stale_drift"
    confidence: float           # 0..1; semantic uses cosine, supersession is always 1.0
    a: ConflictChunk            # the older / superseded / stale side
    b: ConflictChunk            # the newer / superseding / active side
    why: str                    # human-readable one-liner explaining the detection

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "a": self.a.to_dict(),
            "b": self.b.to_dict(),
            "why": self.why,
        }


@dataclass
class ConflictReport:
    conflicts: list[Conflict] = field(default_factory=list)
    db_reachable: bool = True
    error: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.conflicts:
            out[c.kind] = out.get(c.kind, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_reachable": self.db_reachable,
            "error": self.error,
            "params": self.params,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "summary": {
                "total": len(self.conflicts),
                "by_kind": self.by_kind,
            },
        }


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


# Words / phrases that strongly suggest a chunk is overturning, replacing,
# or contradicting earlier reasoning. Case-insensitive substring match on
# the chunk content.
_CONTRADICTION_MARKERS = (
    "instead",
    "no longer",
    "rolled back",
    "rolled-back",
    "rollback",
    "reverted",
    "reverted to",
    "switched from",
    "switched to",
    "actually",
    "rejected",
    "deprecated",
    "abandoned",
    "replaced by",
    "replaced with",
    "obsolete",
    "now using",
    "moved away from",
    "moved to",
    "supersede",
)

_CONTRADICTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _CONTRADICTION_MARKERS) + r")\b",
    re.IGNORECASE,
)


def _has_contradiction_marker(text: str) -> bool:
    return bool(_CONTRADICTION_RE.search(text or ""))


# The same marker set as a POSIX regex for Postgres' case-insensitive `~*`.
# Derived from _CONTRADICTION_MARKERS so the SQL predicate and the Python
# predicate cannot drift apart — `test_sql_and_python_markers_agree` asserts
# they classify identically.
#
# Why it exists: _semantic_conflicts used to apply the marker test in Python,
# *after* the database had already materialised every high-similarity pair.
# Pushing it into the join collapses an all-pairs self-join (|group|^2 vector
# distance computations) down to |marker-bearing chunks| x |group|.
_CONTRADICTION_SQL_RE = r"\m(" + "|".join(_CONTRADICTION_MARKERS) + r")\M"


# ---------------------------------------------------------------------------
# DB connect (mirrors throughline.status._connect so config stays consistent)
# ---------------------------------------------------------------------------


def _connect():
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
        conn = psycopg2.connect(**cfg)
        # Autocommit: a swallowed query error must not leave the transaction
        # aborted and poison every subsequent (read-only) query.
        conn.autocommit = True
        return conn
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Three detection strategies
# ---------------------------------------------------------------------------


# The shared SELECT used to materialise a chunk side of a conflict. It joins
# memory_chunks → messages → conversations to recover the originating tool.
# We use COALESCE(LEFT(content, 500), '') to keep payloads small for the
# CLI / JSON output; the full content is one query away if a consumer
# wants it.
_CHUNK_FIELDS = """
    mc.id                                AS chunk_id,
    COALESCE(c.entrypoint, 'unknown')    AS tool,
    mc.project_name                      AS project,
    mc.category::text                    AS category,
    LEFT(mc.content, 500)                AS content,
    mc.created_at                        AS created_at,
    mc.status                            AS status
"""


def _supersession_conflicts(cur, *, project: str | None) -> list[Conflict]:
    """Strategy 1: chunks explicitly superseded across tool boundaries.

    Surfaces cases where the reflection pass / manual edit / MCP supersede
    tool recorded chunk A as superseded by chunk B, AND the two chunks
    originate from different tools.
    """
    sql = f"""
        SELECT
            {_CHUNK_FIELDS.replace('mc.', 'a.').replace('c.', 'ca.')},
            ab.chunk_id    AS b_chunk_id,
            ab.tool        AS b_tool,
            ab.project     AS b_project,
            ab.category    AS b_category,
            ab.content     AS b_content,
            ab.created_at  AS b_created_at,
            ab.status      AS b_status
        FROM public.memory_chunks a
        JOIN public.messages       am ON am.id = a.source_id
        JOIN public.conversations  ca ON ca.id = am.conversation_id
        JOIN LATERAL (
            SELECT {_CHUNK_FIELDS.replace('mc.', 'b.').replace('c.', 'cb.')}
            FROM public.memory_chunks b
            JOIN public.messages       bm ON bm.id = b.source_id
            JOIN public.conversations  cb ON cb.id = bm.conversation_id
            WHERE b.id = a.superseded_by
        ) ab ON true
        WHERE a.superseded_by IS NOT NULL
          AND ab.tool <> COALESCE(ca.entrypoint, 'unknown')
          {"AND a.project_name = %(project)s" if project else ""}
        ORDER BY a.superseded_at DESC NULLS LAST, a.id DESC
        LIMIT 500
    """
    params = {"project": project} if project else {}
    cur.execute(sql, params)
    out: list[Conflict] = []
    for row in cur.fetchall():
        a = ConflictChunk(
            chunk_id=int(row[0]),
            tool=str(row[1]),
            project=row[2],
            category=str(row[3]),
            content=str(row[4] or ""),
            created_at=row[5].isoformat() if row[5] else "",
            status=str(row[6] or ""),
        )
        b = ConflictChunk(
            chunk_id=int(row[7]),
            tool=str(row[8]),
            project=row[9],
            category=str(row[10]),
            content=str(row[11] or ""),
            created_at=row[12].isoformat() if row[12] else "",
            status=str(row[13] or ""),
        )
        out.append(Conflict(
            kind="supersession",
            confidence=1.0,
            a=a, b=b,
            why=f"chunk #{a.chunk_id} (from {a.tool}) was explicitly superseded by chunk #{b.chunk_id} (from {b.tool})",
        ))
    return out


def _semantic_conflicts(cur, *, project: str | None, min_similarity: float) -> list[Conflict]:
    """Strategy 2: high-similarity cross-tool pairs whose newer side has a contradiction marker.

    Joins via the ``embeddings`` table (768-dim Ollama OR 1536-dim OpenAI;
    whichever is populated). Returns pairs from different tools, same
    project, same category, with cosine similarity >= ``min_similarity``,
    where the *newer* side's content contains at least one contradiction
    marker.

    Confidence = cosine similarity. The contradiction-marker filter is a
    precision lever — without it we'd surface every near-duplicate across
    tools, which is noise, not signal.
    """
    # The cosine distance operator <=> exists for any pgvector column; we
    # try the 768-dim column first (Ollama is the default), then 1536-dim.
    # Both queries are identical except for the column name; we union.
    parts: list[Conflict] = []
    for vec_col in ("embedding_768", "embedding_1536"):
        sql = f"""
            WITH chunk_vec AS (
                SELECT
                    mc.id AS chunk_id,
                    mc.project_name,
                    mc.category::text AS category,
                    LEFT(mc.content, 500) AS content,
                    mc.content AS full_content,
                    mc.created_at,
                    mc.status,
                    COALESCE(c.entrypoint, 'unknown') AS tool,
                    e.{vec_col} AS vec
                FROM public.memory_chunks mc
                JOIN public.messages       m  ON m.id = mc.source_id
                JOIN public.conversations  c  ON c.id = m.conversation_id
                JOIN public.embeddings     e  ON e.source_type = 'memory_chunk'
                                              AND e.source_id   = mc.id
                WHERE mc.status = 'active'
                  AND e.{vec_col} IS NOT NULL
                  AND mc.category IN ('decision', 'pattern', 'insight')
                  {"AND mc.project_name = %(project)s" if project else ""}
            )
            SELECT
                a.chunk_id, a.tool, a.project_name, a.category, a.content,
                a.created_at, a.status,
                b.chunk_id, b.tool, b.project_name, b.category, b.content,
                b.created_at, b.status, b.full_content,
                1 - (a.vec <=> b.vec) AS cosine_sim
            FROM chunk_vec b
            JOIN chunk_vec a
              ON a.project_name = b.project_name
             AND a.category     = b.category
             AND a.tool        <> b.tool
             AND a.chunk_id     < b.chunk_id      -- pair each unordered pair once
             AND a.created_at  <= b.created_at    -- a is the older side
             AND 1 - (a.vec <=> b.vec) >= %(min_sim)s
            -- The newer side must carry a contradiction marker. This used to
            -- run in Python over every returned pair; as a join predicate it
            -- shrinks the driving side to the few chunks that can possibly
            -- produce a conflict, instead of computing a vector distance for
            -- every pair in the project+category group.
            WHERE b.full_content ~* %(marker_re)s
            ORDER BY cosine_sim DESC
            LIMIT 500
        """
        params = {"min_sim": float(min_similarity), "marker_re": _CONTRADICTION_SQL_RE}
        if project:
            params["project"] = project
        try:
            cur.execute(sql, params)
        except Exception:
            # Most likely: this embedding column doesn't exist on this
            # install (single-backend setup). Try the other one.
            continue
        for row in cur.fetchall():
            full_b_content = str(row[14] or "")
            if not _has_contradiction_marker(full_b_content):
                continue
            a = ConflictChunk(
                chunk_id=int(row[0]), tool=str(row[1]), project=row[2],
                category=str(row[3]), content=str(row[4] or ""),
                created_at=row[5].isoformat() if row[5] else "",
                status=str(row[6] or ""),
            )
            b = ConflictChunk(
                chunk_id=int(row[7]), tool=str(row[8]), project=row[9],
                category=str(row[10]), content=str(row[11] or ""),
                created_at=row[12].isoformat() if row[12] else "",
                status=str(row[13] or ""),
            )
            parts.append(Conflict(
                kind="semantic",
                confidence=round(float(row[15]), 4),
                a=a, b=b,
                why=(
                    f"{a.tool} and {b.tool} both recorded a {a.category} for project "
                    f"'{a.project}' (cosine={row[15]:.3f}); the newer chunk contains "
                    f"contradiction markers ('{_first_marker(full_b_content)}')"
                ),
            ))
        # If the first column produced rows, don't double-count from the second.
        if parts:
            return parts
    return parts


def _stale_drift_conflicts(cur, *, project: str | None, since_days: int) -> list[Conflict]:
    """Strategy 3: stale chunk from tool A, never accessed, while a newer
    chunk from tool B exists for the same project+category."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    sql = f"""
        WITH chunks AS (
            SELECT
                mc.id AS chunk_id,
                mc.project_name,
                mc.category::text AS category,
                LEFT(mc.content, 500) AS content,
                mc.created_at,
                mc.status,
                mc.access_count,
                COALESCE(c.entrypoint, 'unknown') AS tool
            FROM public.memory_chunks mc
            JOIN public.messages       m  ON m.id = mc.source_id
            JOIN public.conversations  c  ON c.id = m.conversation_id
            WHERE mc.status = 'active'
              {"AND mc.project_name = %(project)s" if project else ""}
        )
        SELECT
            a.chunk_id, a.tool, a.project_name, a.category, a.content, a.created_at, a.status,
            b.chunk_id, b.tool, b.project_name, b.category, b.content, b.created_at, b.status
        FROM chunks a
        JOIN chunks b
          ON a.project_name = b.project_name
         AND a.category     = b.category
         AND a.tool        <> b.tool
         AND a.created_at < b.created_at
        WHERE a.created_at <= %(cutoff)s
          AND a.access_count = 0
        ORDER BY b.created_at DESC
        LIMIT 500
    """
    params: dict[str, Any] = {"cutoff": cutoff}
    if project:
        params["project"] = project
    cur.execute(sql, params)
    out: list[Conflict] = []
    seen: set[tuple[int, int]] = set()
    for row in cur.fetchall():
        a_id, b_id = int(row[0]), int(row[7])
        if (a_id, b_id) in seen:
            continue
        seen.add((a_id, b_id))
        a = ConflictChunk(
            chunk_id=a_id, tool=str(row[1]), project=row[2],
            category=str(row[3]), content=str(row[4] or ""),
            created_at=row[5].isoformat() if row[5] else "",
            status=str(row[6] or ""),
        )
        b = ConflictChunk(
            chunk_id=b_id, tool=str(row[8]), project=row[9],
            category=str(row[10]), content=str(row[11] or ""),
            created_at=row[12].isoformat() if row[12] else "",
            status=str(row[13] or ""),
        )
        out.append(Conflict(
            kind="stale_drift",
            confidence=0.5,  # weakest signal of the three
            a=a, b=b,
            why=(
                f"{a.tool}'s {a.category} for '{a.project}' is >{since_days} days old "
                f"and never accessed, while {b.tool} has a newer {b.category} for the same project"
            ),
        ))
    return out


def _first_marker(text: str) -> str:
    m = _CONTRADICTION_RE.search(text or "")
    return m.group(0) if m else "?"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_conflicts(
    conn=None,
    *,
    project: str | None = None,
    kinds: Iterable[str] | None = None,
    since_days: int = 30,
    min_similarity: float = 0.85,
) -> ConflictReport:
    """Run cross-tool conflict detection. See module docstring for theory.

    Args:
        conn: optional psycopg2 connection (mostly for tests). If None, we
            open one with our own _connect helper and close it on exit.
        project: if set, restrict to this project_name.
        kinds: subset of {"supersession", "semantic", "stale_drift"}.
            Default: all three.
        since_days: stale_drift cutoff (default 30).
        min_similarity: semantic cosine threshold (default 0.85).

    Returns a ConflictReport with structured results. Never raises on
    DB errors; sets report.db_reachable=False and report.error instead.
    """
    requested = set(kinds) if kinds else {"supersession", "semantic", "stale_drift"}

    owns_conn = False
    if conn is None:
        conn = _connect()
        owns_conn = True
        if conn is None:
            return ConflictReport(
                db_reachable=False,
                error="DB unreachable (psycopg2 missing or connection refused)",
                params={
                    "project": project,
                    "kinds": sorted(requested),
                    "since_days": since_days,
                    "min_similarity": min_similarity,
                },
            )

    report = ConflictReport(params={
        "project": project,
        "kinds": sorted(requested),
        "since_days": since_days,
        "min_similarity": min_similarity,
    })
    try:
        with conn.cursor() as cur:
            if "supersession" in requested:
                try:
                    report.conflicts.extend(_supersession_conflicts(cur, project=project))
                except Exception as e:
                    report.error = (report.error or "") + f"supersession: {e}; "
            if "semantic" in requested:
                try:
                    report.conflicts.extend(_semantic_conflicts(
                        cur, project=project, min_similarity=min_similarity
                    ))
                except Exception as e:
                    report.error = (report.error or "") + f"semantic: {e}; "
            if "stale_drift" in requested:
                try:
                    report.conflicts.extend(_stale_drift_conflicts(
                        cur, project=project, since_days=since_days
                    ))
                except Exception as e:
                    report.error = (report.error or "") + f"stale_drift: {e}; "
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:
                pass

    return report


# ---------------------------------------------------------------------------
# Human formatter (used by the CLI)
# ---------------------------------------------------------------------------


_KIND_LABEL = {
    "supersession": "Documented supersession",
    "semantic": "Semantic near-duplicate w/ contradiction marker",
    "stale_drift": "Stale chunk (likely tool drift)",
}


def _truncate(text: str, n: int = 200) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def format_human(report: ConflictReport) -> str:
    """Render the report as a human-readable block grouped by conflict kind."""
    if not report.db_reachable:
        return f"Cannot reach the memory DB: {report.error or 'unknown error'}"

    if not report.conflicts:
        if report.error:
            return (
                "Conflict scan incomplete — no results, but errors occurred:\n"
                f"  {report.error}"
            )
        return (
            "No cross-tool conflicts found"
            + (f" for project '{report.params.get('project')}'." if report.params.get("project") else ".")
            + "\nThis usually means: one tool dominates this workspace, OR the project hasn't accumulated\n"
              "enough multi-tool history yet. Try `throughline conflicts --since-days 90` for a wider window."
        )

    by_kind: dict[str, list[Conflict]] = {}
    for c in report.conflicts:
        by_kind.setdefault(c.kind, []).append(c)

    lines: list[str] = []
    for kind in ("supersession", "semantic", "stale_drift"):
        bucket = by_kind.get(kind, [])
        if not bucket:
            continue
        lines.append(f"── {_KIND_LABEL.get(kind, kind)} ({len(bucket)}) ──")
        for c in bucket:
            lines.append(
                f"  {c.a.tool} → {c.b.tool}    "
                f"project={c.a.project or '?'}    category={c.a.category}    "
                f"conf={c.confidence:.2f}"
            )
            lines.append(f"    A (#{c.a.chunk_id}, {c.a.created_at[:10]}): {_truncate(c.a.content)}")
            lines.append(f"    B (#{c.b.chunk_id}, {c.b.created_at[:10]}): {_truncate(c.b.content)}")
            lines.append(f"    why: {c.why}")
            lines.append("")

    counts = ", ".join(f"{k}={v}" for k, v in sorted(report.by_kind.items()))
    lines.append(f"Summary: {len(report.conflicts)} conflict(s) — {counts}")
    return "\n".join(lines)
