"""Provider coverage: what exists on disk against what is imported.

`pending` — discovered files with no `ingestion_log` entry that ingestion
*would* process — is the column that matters. `on_disk` alone is misleading:
Claude Code rotates its transcripts, so conversations persist after their
files are gone and `ingested` can legitimately exceed `on_disk`. `status`
therefore derives from `pending`, never from `ingested == 0`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from throughline import providers as P
from throughline.adapters.registry import all_adapters
from throughline.queries._exec import rows

#: The scan walks ~300 paths and hashes nothing, so it is cheap — but it
#: changes when you ingest, not per request, and Overview polls while a job
#: runs. Sixty seconds is long enough to absorb the polling and short enough
#: that a finished ingest shows up without a restart.
CACHE_TTL_SECONDS = 60

_cache: tuple[float, dict[str, "DiskCounts"]] | None = None


@dataclass(frozen=True)
class DiskCounts:
    on_disk: int
    pending: int
    excluded: int
    present: bool


def invalidate_scan_cache() -> None:
    """Call after an ingest so coverage reflects it immediately."""
    global _cache
    _cache = None


def _scan_uncached() -> dict[str, DiskCounts]:
    from throughline.adapters.writer import _connect

    ingested_paths: set[str] = set()
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT file_path FROM ingestion_log")
                ingested_paths = {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        # A scan that cannot reach the database still reports disk truth.
        ingested_paths = set()

    out: dict[str, DiskCounts] = {}
    for adapter in all_adapters():
        try:
            every = [str(p) for p in adapter.discover_all()]
            ingestable = {str(p) for p in adapter.discover()}
        except Exception:
            out[adapter.name] = DiskCounts(0, 0, 0, False)
            continue
        pending = sum(1 for p in every if p in ingestable and p not in ingested_paths)
        out[adapter.name] = DiskCounts(
            on_disk=len(every),
            pending=pending,
            excluded=len(every) - len(ingestable),
            present=bool(every),
        )
    return out


def _disk_scan() -> dict[str, DiskCounts]:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    scanned = _scan_uncached()
    _cache = (now, scanned)
    return scanned


def _status(disk: DiskCounts, ingested: int) -> str:
    if not disk.present and ingested == 0:
        return "no_data"
    if disk.pending > 0 and ingested == 0:
        return "not_ingested"
    if disk.pending > 0:
        return "pending"
    return "ok"


def coverage(conn) -> list[dict]:
    counts = {
        r["source_tool"]: r
        for r in rows(
            conn,
            """
            SELECT source_tool, count(*) AS ingested, max(started_at) AS last_run
            FROM conversations
            GROUP BY source_tool
            """,
        )
    }
    disk = _disk_scan()

    out: list[dict] = []
    for prov in P.PROVIDERS:
        c = counts.get(prov.name) or {}
        d = disk.get(prov.name) or DiskCounts(0, 0, 0, False)
        ingested = int(c.get("ingested") or 0)
        out.append(
            {
                "name": prov.name,
                "label": prov.label,
                "chart_slot": prov.chart_slot,
                "on_disk": d.on_disk,
                "pending": d.pending,
                "excluded": d.excluded,
                "ingested": ingested,
                "last_run": c.get("last_run"),
                "status": _status(d, ingested),
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
