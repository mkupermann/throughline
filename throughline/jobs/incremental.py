"""Bounded, source-versioned enrichment with atomic per-conversation checkpoints."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2

from throughline.config import get_db_config
from throughline.queries._exec import one, rows
from throughline.queries.projects import project_filter_params, project_filter_sql, project_name_sql

# Include roles, content, edits and deletions; token counts/timestamps alone miss edits.
FINGERPRINT = """md5(COALESCE((SELECT string_agg(md5(m.role::text || ':' || COALESCE(m.content,'')), '' ORDER BY m.id)
 FROM messages m WHERE m.conversation_id=c.id AND m.role IN ('user','assistant')), '')
 || ':' || COALESCE(c.assigned_project,c.project_name,''))"""


def pending(conn, stage: str, project: str | None = None, limit: int = 50, since=None):
    if stage not in ("extract", "entities"):
        raise ValueError("Unknown enrichment stage")
    return rows(
        conn,
        f"""
        SELECT c.id, {project_name_sql()} AS project_name,
               c.message_count, {FINGERPRINT} AS fingerprint
        FROM conversations c
        LEFT JOIN processing_checkpoints p ON p.conversation_id=c.id AND p.stage=%(stage)s
        WHERE c.generated_by IS NULL AND c.message_count >= 5
          AND (%(project)s IS NULL OR {project_filter_sql()})
          AND (%(since)s IS NULL OR c.started_at >= %(since)s::date)
          AND (p.fingerprint IS NULL OR p.fingerprint <> {FINGERPRINT})
        ORDER BY c.started_at DESC NULLS LAST,c.id DESC LIMIT %(limit)s
    """,
        {**project_filter_params(project), "stage": stage, "limit": limit, "since": since},
    )


def model_label():
    from throughline import ai_runtime, llm

    selected = ai_runtime.route("extraction")
    info = llm.backend_info(purpose="extraction")
    if not info.available:
        raise RuntimeError(info.detail)
    return f"{info.backend}/{info.model}", bool(selected and selected.get("cli")) or info.backend in (
        "claude",
        "codex",
        "vibe",
    )


def process_item(item, stage, model, connect=None):
    connect = connect or (lambda: psycopg2.connect(**get_db_config()))
    conn = connect()
    started = time.monotonic()
    try:
        with conn:
            # Serialize this source/stage across callers without holding a source-row lock during AI I/O.
            one(conn, "SELECT pg_advisory_xact_lock(%s,%s)", (139410 if stage == "extract" else 139411, item["id"]))
            saved = one(
                conn,
                "SELECT fingerprint FROM processing_checkpoints WHERE stage=%s AND conversation_id=%s",
                (stage, item["id"]),
            )
            if saved and saved["fingerprint"] == item["fingerprint"]:
                return {"skipped": True, "seconds": 0}
            with conn.cursor() as cur:
                if stage == "extract":
                    from throughline.jobs.extract_memory import extract_for_conversation

                    cur.execute(
                        "SELECT id FROM memory_chunks WHERE source_type='conversation' AND source_id=%s AND COALESCE(status,'active')='active'",
                        (item["id"],),
                    )
                    prior = [r[0] for r in cur.fetchall()]
                    count = extract_for_conversation(cur, item["id"])
                    # Retain historical evidence; superseded source versions are visible through curation.
                    if prior and count:
                        cur.execute("UPDATE memory_chunks SET status='stale' WHERE id=ANY(%s)", (prior,))
                else:
                    from throughline.jobs.extract_entities import extract_for_conversation

                    count = sum(extract_for_conversation(cur, item["id"], item["project_name"]))
            current = one(
                conn, f"SELECT {FINGERPRINT} AS fingerprint FROM conversations c WHERE c.id=%s", (item["id"],)
            )
            if not current or current["fingerprint"] != item["fingerprint"]:
                raise RuntimeError("Source changed during extraction; retry with its new version")
            elapsed = time.monotonic() - started
            one(
                conn,
                """INSERT INTO processing_checkpoints(stage,conversation_id,fingerprint,model,elapsed_seconds,output_count)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(stage,conversation_id) DO UPDATE SET
                fingerprint=EXCLUDED.fingerprint, model=EXCLUDED.model, elapsed_seconds=EXCLUDED.elapsed_seconds,
                output_count=EXCLUDED.output_count, completed_at=now() RETURNING conversation_id""",
                (stage, item["id"], item["fingerprint"], model, elapsed, count),
            )
        return {"skipped": False, "seconds": elapsed, "output_count": count}
    finally:
        conn.close()


def run_stage(stage, *, limit=50, project=None, workers=1, since=None, dry_run=False):
    conn = psycopg2.connect(**get_db_config())
    try:
        items = pending(conn, stage, project, limit, since)
    finally:
        conn.close()
    print(f"{stage}: {len(items)} pending source versions selected (limit={limit}).", flush=True)
    if dry_run or not items:
        return 0
    model, cli = model_label()
    workers = 1 if cli else max(1, min(workers, 4))
    print(
        f"Model: {model}; workers: {workers}. Successful source versions, including empty results, are retained.",
        flush=True,
    )
    errors = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_item, item, stage, model): item["id"] for item in items}
        for done, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result()
                outcome = "already completed" if result["skipped"] else f"{result['output_count']} results"
            except Exception as exc:
                errors += 1
                outcome = f"failed ({type(exc).__name__}); source remains pending"
            elapsed = time.monotonic() - started
            remaining = (len(items) - done) * elapsed / done
            print(
                f"[{done}/{len(items)}] #{futures[future]} {outcome}; elapsed {elapsed:.0f}s; estimated remaining ~{remaining:.0f}s",
                flush=True,
            )
    return errors


def main():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=["extract", "entities"])
    p.add_argument("--limit", type=int, default=int(os.environ.get("THROUGHLINE_PROCESS_LIMIT", "50")))
    p.add_argument("--project", default=os.environ.get("THROUGHLINE_PROCESS_PROJECT") or None)
    p.add_argument("--workers", type=int, default=int(os.environ.get("THROUGHLINE_PROCESS_WORKERS", "1")))
    p.add_argument("--since")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.limit < 1 or not 1 <= args.workers <= 4:
        p.error("Use a positive limit and 1–4 workers")
    errors = 0
    for stage in [args.stage] if args.stage else ["extract", "entities"]:
        errors += run_stage(
            stage, limit=args.limit, project=args.project, workers=args.workers, since=args.since, dry_run=args.dry_run
        )
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
