"""Provider coverage: what exists on disk against what is imported.

`pending` — discovered files with no `ingestion_log` entry that ingestion
*would* process — is the column that matters. `on_disk` alone is misleading:
Claude Code rotates its transcripts, so conversations persist after their
files are gone and `ingested` can legitimately exceed `on_disk`. `status`
therefore derives from `pending`, never from `ingested == 0` alone.

This module is the one place a wrong number is worse than a slow one: its
whole job is telling the user what is and is not imported. Two rules follow
from that:

- The filesystem walk is the only thing cached (``_disk_scan``/TTL below).
  ``ingestion_log`` is always read live, through the caller's own
  already-proven-working connection, never a second connection opened here
  — a second connection can fail silently in a way the first one didn't,
  and an empty ``ingested_paths`` set would make every ingestable file look
  pending. That failure must surface as the router's normal 503, not as a
  plausible-looking wrong count.
- A row that could not be scanned (a broken adapter, a permission error)
  reports ``status: "unknown"``, never ``"no_data"``. Those look identical
  to a human unless kept apart on purpose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from throughline import providers as P
from throughline.adapters.registry import all_adapters
from throughline.queries._exec import rows

#: The scan walks ~300 paths and hashes nothing, so it is cheap — but it
#: changes when you ingest, not per request, and Overview polls while a job
#: runs. Sixty seconds is long enough to absorb the polling and short enough
#: that a finished ingest shows up without a restart. Only the filesystem
#: side is cached; `pending` is computed fresh every request (see module
#: docstring), so it is never stale even within the TTL window.
CACHE_TTL_SECONDS = 60

_cache: tuple[float, dict[str, "DiskCounts"]] | None = None


@dataclass(frozen=True)
class DiskCounts:
    """Filesystem truth for one provider. Nothing here touches the database.

    ``ingestable_paths`` is what ``coverage()`` diffs against a live
    ``ingestion_log`` read to get `pending` — kept as a set rather than a
    precomputed count so that diff can happen against the caller's
    connection instead of one opened inside the (cached) scan.
    """

    on_disk: int
    excluded: int
    present: bool
    ingestable_paths: frozenset[str] = field(default_factory=frozenset)
    #: adapter.discover()/discover_all() raised. Distinct from "found
    #: nothing" — a permission error or an adapter bug must never render as
    #: `no_data`, which reads to a human as "this tool has nothing here".
    error: bool = False


def invalidate_scan_cache() -> None:
    """Call after an ingest so coverage reflects it immediately."""
    global _cache
    _cache = None


def _scan_uncached() -> dict[str, DiskCounts]:
    out: dict[str, DiskCounts] = {}
    for adapter in all_adapters():
        try:
            every = [str(p) for p in adapter.discover_all()]
            ingestable = frozenset(str(p) for p in adapter.discover())
        except Exception:
            out[adapter.name] = DiskCounts(on_disk=0, excluded=0, present=False, error=True)
            continue
        out[adapter.name] = DiskCounts(
            on_disk=len(every),
            excluded=len(every) - len(ingestable),
            present=bool(every),
            ingestable_paths=ingestable,
        )
    return out


def _disk_scan() -> dict[str, DiskCounts]:
    global _cache
    cached = _cache  # snapshot once: invalidate_scan_cache() may run between
    now = time.monotonic()  # the condition check and the return below.
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    scanned = _scan_uncached()
    _cache = (now, scanned)
    return scanned


def _status(disk: DiskCounts, pending: int, ingested: int) -> str:
    if disk.error:
        return "unknown"
    if not disk.present and ingested == 0:
        return "no_data"
    if pending > 0 and ingested == 0:
        return "not_ingested"
    if pending > 0:
        return "pending"
    if ingested == 0:
        # Files existed but every one was excluded (e.g. subagent-only
        # transcripts), or ingestion_log rows outlived their conversations.
        # Either way nothing is currently imported and nothing is pending
        # to fix that — the same "nothing to act on" state as no_data, just
        # reached from a present source instead of an absent one. `on_disk`
        # and `excluded` on the row still say why, for a UI that wants to.
        return "no_data"
    return "ok"


def coverage(conn) -> list[dict]:
    counts = {
        r["source_tool"]: r
        for r in rows(
            conn,
            """
            -- Sessions a person had, per tool. The chip reads as "how much of
            -- my work came through this assistant"; counting the tool's own
            -- `claude -p` calls made Claude Code show 3,439 where 172 were
            -- real, which is not a coverage figure but a measure of how often
            -- Throughline ran.
            SELECT source_tool,
                   count(*) FILTER (WHERE generated_by IS NULL) AS ingested,
                   count(*) FILTER (WHERE generated_by IS NOT NULL) AS generated,
                   -- When Throughline last WROTE something for this tool,
                   -- not when the newest session happened to begin.
                   --
                   -- This was `max(started_at)`, which answers a different
                   -- question and answers it misleadingly under a column
                   -- headed "Last run": a long session keeps the start time it
                   -- opened with, so someone working in the same session for
                   -- four days sees a date four days old and concludes
                   -- ingestion has stopped. It had not — every file on disk
                   -- was imported and the session was being refreshed hourly.
                   -- `updated_at` moves whenever a row is written or
                   -- refreshed, which is what "did this tool import lately?"
                   -- actually means.
                   max(updated_at) FILTER (WHERE generated_by IS NULL) AS last_run
            FROM conversations
            GROUP BY source_tool
            """,
        )
    }
    disk = _disk_scan()
    # Live, through the caller's own connection — see module docstring.
    ingested_paths = {
        r["file_path"] for r in rows(conn, "SELECT file_path FROM ingestion_log")
    }

    out: list[dict] = []
    for prov in P.PROVIDERS:
        c = counts.get(prov.name) or {}
        d = disk.get(prov.name) or DiskCounts(on_disk=0, excluded=0, present=False)
        ingested = int(c.get("ingested") or 0)
        pending = 0 if d.error else len(d.ingestable_paths - ingested_paths)
        out.append(
            {
                "name": prov.name,
                "label": prov.label,
                "chart_slot": prov.chart_slot,
                "on_disk": d.on_disk,
                "pending": pending,
                "excluded": d.excluded,
                "ingested": ingested,
                "last_run": c.get("last_run"),
                "status": _status(d, pending, ingested),
            }
        )

    unattributed = counts.get(None)
    if unattributed and int(unattributed.get("ingested") or 0) > 0:
        out.append(
            {
                "name": "(unattributed)",
                "label": P.UNATTRIBUTED_LABEL,
                "chart_slot": 0,
                "on_disk": 0,
                "pending": 0,
                "excluded": 0,
                "ingested": int(unattributed["ingested"]),
                "last_run": unattributed.get("last_run"),
                "status": "unknown",
            }
        )
    return out
