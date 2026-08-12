"""The /overview surface: a worklist, not a metric wall.

The Streamlit dashboard rendered eleven equally-weighted tiles and left the
reader to work out which number mattered. This endpoint inverts that: it
returns one headline, one verdict, and a list of *attention items* — things
that are actually wrong and what to do about them. When nothing is wrong the
list is empty and the UI can say so in one line.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends

from throughline import queries as Q
from throughline.status import collect_status

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["overview"])

Severity = Literal["critical", "warning", "info"]


@dataclass
class AttentionItem:
    """One thing that needs a human. `action` is a UI route, not prose."""

    id: str
    severity: Severity
    title: str
    detail: str
    count: int | None = None
    action: str | None = None
    action_label: str | None = None


@dataclass
class Overview:
    headline: dict[str, Any]
    verdict: Literal["ok", "degraded", "broken"]
    verdict_reason: str
    attention: list[AttentionItem] = field(default_factory=list)
    activity: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    categories: list[dict[str, Any]] = field(default_factory=list)


#: An embedding coverage below this is worth surfacing; semantic search
#: silently misses whatever is not embedded, so it is not self-evident.
COVERAGE_WARN_PCT = 90.0
#: Ingestion older than this suggests the pipeline stopped running.
STALE_INGEST_DAYS = 7
#: Chunks below this confidence are worth a human's review.
LOW_CONFIDENCE = 0.6


def _build_overview(conn, settings: Settings) -> Overview:
    status = collect_status(conn=conn)
    totals = Q.conversations.totals(conn)
    counts = Q.memory.status_counts(conn)

    attention: list[AttentionItem] = []
    verdict: Literal["ok", "degraded", "broken"] = "ok"
    verdict_reason = "Everything looks healthy."

    # pgvector can be listed in the catalogue while its library is missing —
    # a Homebrew major-version bump does exactly that. Every vector query
    # then fails, so check it before anything that depends on embeddings.
    vector_ok = Q.health.vector_extension_ok(conn)
    if not vector_ok:
        verdict = "broken"
        verdict_reason = "pgvector is installed but its library cannot be loaded."
        attention.append(
            AttentionItem(
                id="pgvector-broken",
                severity="critical",
                title="pgvector is broken",
                detail=(
                    "The extension is registered but the shared library is missing, so "
                    "every query touching a vector column fails. Semantic search and "
                    "embedding are unavailable until it is reinstalled for this server "
                    "version."
                ),
                action="/operate",
                action_label="Diagnostics",
            )
        )

    # A schema the running code does not expect is the one fault that can damage
    # the archive rather than merely degrade a query — and this database is the
    # only surviving copy of most of what it holds, because the source CLIs
    # rotate their transcripts away. Ranked directly below pgvector for that
    # reason. `None` means the check could not run and is deliberately silent:
    # claiming all-clear without looking is the failure this replaced.
    pending_migrations = status.get("pending_migrations")
    if pending_migrations:
        verdict = "broken" if verdict == "broken" else "degraded"
        attention.append(
            AttentionItem(
                id="pending-migrations",
                severity="critical",
                title="Schema migrations pending",
                detail=(
                    "The database schema is older than the code reading it: "
                    + ", ".join(pending_migrations)
                    + ". Apply them with `python3 scripts/migrate.py` before the next "
                    "ingest — a migration left unapplied has already cost silently "
                    "dropped messages once."
                ),
                count=len(pending_migrations),
                action="/operate",
                action_label="Diagnostics",
            )
        )

    contradictions = int(status.get("contradictions_outstanding") or 0)
    if contradictions:
        verdict = "degraded" if verdict == "ok" else verdict
        attention.append(
            AttentionItem(
                id="contradictions",
                severity="warning",
                title="Contradictions outstanding",
                detail="Memory chunks that disagree with each other across tools.",
                count=contradictions,
                action="/curate?queue=contradictions",
                action_label="Review",
            )
        )

    if vector_ok:
        coverage = Q.health.embedding_coverage(conn)
        total, embedded = int(coverage["total"] or 0), int(coverage["embedded"] or 0)
        pct = (100.0 * embedded / total) if total else 100.0
        if total and pct < COVERAGE_WARN_PCT:
            verdict = "degraded" if verdict == "ok" else verdict
            attention.append(
                AttentionItem(
                    id="embedding-gap",
                    severity="warning",
                    title="Memory is not fully embedded",
                    detail=(
                        f"{total - embedded:,} of {total:,} active chunks have no embedding "
                        f"and cannot be found by semantic search ({pct:.0f}% covered)."
                    ),
                    count=total - embedded,
                    action="/operate?job=embed",
                    action_label="Embed now",
                )
            )

    last_ingest = Q.health.last_ingestion_at(conn)
    if last_ingest is None:
        attention.append(
            AttentionItem(
                id="never-ingested",
                severity="info",
                title="Nothing ingested yet",
                detail="Run an ingestion to pull sessions in from your AI coding tools.",
                action="/operate?job=ingest",
                action_label="Ingest",
            )
        )
    else:
        from datetime import datetime, timezone

        age_days = (datetime.now(timezone.utc) - last_ingest).days
        if age_days >= STALE_INGEST_DAYS:
            attention.append(
                AttentionItem(
                    id="stale-ingest",
                    severity="info",
                    title="Ingestion is stale",
                    detail=f"Last ingestion ran {age_days} days ago.",
                    count=age_days,
                    action="/operate?job=ingest",
                    action_label="Ingest",
                )
            )

    pending = Q.health.pending_extraction(conn, min_messages=5)
    if pending:
        attention.append(
            AttentionItem(
                id="pending-extraction",
                severity="info",
                title="Conversations awaiting extraction",
                detail="Sessions long enough to hold memory that has not been extracted yet.",
                count=pending,
                action="/operate?job=extract",
                action_label="Extract",
            )
        )

    low_conf = Q.memory.queue_low_confidence(conn, threshold=LOW_CONFIDENCE, limit=1000)
    if low_conf:
        attention.append(
            AttentionItem(
                id="low-confidence",
                severity="info",
                title="Low-confidence memory",
                detail=f"Chunks stored below {LOW_CONFIDENCE:.0%} confidence.",
                count=len(low_conf),
                action=f"/curate?queue=low-confidence",
                action_label="Review",
            )
        )

    # 8,453 messages once sat on disk, fully parseable, one command away —
    # and nothing in the product ever said so. A provider with files on disk
    # and zero rows imported gets its own attention item, one per provider,
    # pointing at Operate rather than silently waiting to be noticed.
    from throughline.queries import providers as PQ

    for row in PQ.coverage(conn):
        if row["status"] != "not_ingested":
            continue
        attention.append(
            AttentionItem(
                id=f"provider-not-ingested-{row['name']}",
                severity="warning",
                title=f"{row['label']}: {row['pending']} file(s) on disk, 0 ingested",
                detail=(
                    f"{row['label']} data is present and parseable but has never been "
                    f"imported. Run the {row['label']} ingest to bring it in."
                ),
                count=row["pending"],
                action="/operate",
                action_label=f"Ingest {row['label']}",
            )
        )

    if attention:
        n = len(attention)
        plural = "item needs" if n == 1 else "items need"
        if verdict == "ok":
            verdict = "degraded"
        if verdict != "broken":
            verdict_reason = f"{n} {plural} attention."

    activity = [
        {"day": str(r["day"]), "n": int(r["n"])}
        for r in Q.activity.conversations_per_day(conn, days=30)
    ]

    return Overview(
        # The number and the words around it have to explain each other. This
        # read "Memory chunks under management" over the figure 800, with
        # "1,019 total, all statuses" beneath — three quantities, no stated
        # relationship, and no way to tell why the big number was not the total.
        # Now the label names what the figure counts and the sublabel says what
        # the remainder is, so the two numbers account for each other.
        headline={
            "label": "Active memory",
            "value": int(counts.get("active") or 0),
            "sublabel": (
                f"of {int(counts.get('total') or 0):,} stored — the rest is "
                "superseded, expired or forgotten"
            ),
        },
        verdict=verdict,
        verdict_reason=verdict_reason,
        attention=attention,
        activity=activity,
        totals={
            "conversations": int(totals.get("conv") or 0),
            "messages": int(totals.get("msg") or 0),
            "chunks": int(totals.get("mem") or 0),
            "skills": int(totals.get("sk") or 0),
            "projects": int(status.get("projects_count") or 0),
        },
        # What KIND of memory this is, not just how much. The headline says
        # 977 chunks; this says whether they are decisions, errors solved, or
        # contacts — the shape the Streamlit dashboard showed and the rebuild
        # dropped. Sent with the rest of the payload rather than fetched
        # separately: one screen, one request.
        categories=[
            {"category": r["category"] or "uncategorised", "n": int(r["n"])}
            for r in Q.memory.category_counts(conn)
        ],
    )


@router.get("/overview")
def get_overview(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    with connection(settings) as conn:
        overview = _build_overview(conn, settings)
    payload = asdict(overview)
    payload["attention"] = [asdict(a) if not isinstance(a, dict) else a for a in overview.attention]
    return payload
