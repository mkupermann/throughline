#!/usr/bin/env python3
"""Repair existing ``conversations`` rows from the original JSONL files.

Two ingest bugs left bad data on disk:

1. ``project_path`` was reconstructed from Claude Code's session-hash
   directory name by replacing every ``-`` with ``/``. Any project
   whose name contains a hyphen (``claude-memory-db``,
   ``daily-mail-drafter``, …) ends up stored as a corrupted path with
   the hyphenated component split into directory levels.

2. ``token_count_in`` / ``token_count_out`` were never populated; the
   columns sat at NULL/0 even though the JSONL ``usage`` blocks carried
   the real numbers all along.

This script fixes both, in place, by re-reading the original JSONL files
referenced via ``ingestion_log``. Idempotent (an ``UPDATE`` is only
issued when at least one column would change), safe to re-run, and
``--dry-run`` previews the diff without writing.

Usage::

    python scripts/repair_conversations.py             # full repair
    python scripts/repair_conversations.py --dry-run   # preview only
    python scripts/repair_conversations.py --limit 50  # cap rows touched

    # Same options via the unified CLI:
    throughline repair-conversations [--dry-run] [--limit N]
"""
from __future__ import annotations
from _bootstrap import use_venv  # noqa: E402
use_venv()


import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2


def db_config() -> dict:
    cfg = {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "throughline"),
        "user": os.environ.get("PGUSER", os.environ.get("USER") or "postgres"),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", "5")),
    }
    pw = os.environ.get("PGPASSWORD")
    if pw:
        cfg["password"] = pw
    return cfg


def parse_jsonl(path: Path) -> tuple[str | None, int, int, str | None]:
    """Return (cwd, tokens_in, tokens_out, session_id) for a JSONL file.

    cwd / session_id are None if the file is empty / unreadable. tokens_*
    are zero in the same case. Skips malformed lines silently — the
    ingest pipeline already prints a per-line error during the original
    ingest, so doing it twice would be noisy.
    """
    cwd: str | None = None
    in_total = 0
    out_total = 0
    session_id: str | None = None
    if not path.exists():
        return None, 0, 0, None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cwd is None:
                c = d.get("cwd")
                if isinstance(c, str) and c.strip():
                    cwd = c
            if session_id is None:
                s = d.get("sessionId")
                if isinstance(s, str) and s:
                    session_id = s
            m = d.get("message")
            if isinstance(m, dict):
                u = m.get("usage")
                if isinstance(u, dict):
                    in_total += (
                        int(u.get("input_tokens") or 0)
                        + int(u.get("cache_creation_input_tokens") or 0)
                        + int(u.get("cache_read_input_tokens") or 0)
                    )
                    out_total += int(u.get("output_tokens") or 0)
    return cwd, in_total, out_total, session_id


def repair_session(conn, *, session_id: str, file_paths: list[str], dry_run: bool) -> dict:
    """Repair a single session by aggregating across ALL its JSONL files.

    Subagent JSONLs share their parent's ``sessionId``; processing each
    file independently and overwriting the conversations row meant
    last-file-wins, which was both wrong (subagent tokens dropped) and
    non-idempotent (file iteration order depended on ``ingested_at``).
    Aggregating across the full set of files for one session_id gives
    the correct total and lands the same number on every re-run.

    Returns one of: skipped / no-change / updated / would-update.
    """
    cwd: str | None = None
    in_total = 0
    out_total = 0
    seen_any = False
    for fp in file_paths:
        c, ti, to, _ = parse_jsonl(Path(fp))
        if c is None and ti == 0 and to == 0:
            continue
        seen_any = True
        if cwd is None and c is not None:
            cwd = c
        in_total += ti
        out_total += to
    if not seen_any:
        return {"status": "skipped", "reason": "no-readable-jsonl"}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, project_path, token_count_in, token_count_out "
            "FROM public.conversations WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "skipped", "reason": "row-not-found"}
    conv_id, old_path, old_in, old_out = row
    old_in = int(old_in or 0)
    old_out = int(old_out or 0)

    new_path = cwd if cwd else old_path
    new_in = in_total or old_in
    new_out = out_total or old_out

    changes: dict[str, tuple] = {}
    if new_path != old_path and new_path is not None:
        changes["project_path"] = (old_path, new_path)
    if new_in != old_in:
        changes["token_count_in"] = (old_in, new_in)
    if new_out != old_out:
        changes["token_count_out"] = (old_out, new_out)
    if not changes:
        return {"status": "no-change"}

    if dry_run:
        return {"status": "would-update", "id": conv_id, "changes": changes}

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.conversations "
            "SET project_path = %s, token_count_in = %s, token_count_out = %s "
            "WHERE id = %s",
            (new_path, new_in, new_out, conv_id),
        )
    conn.commit()
    return {"status": "updated", "id": conv_id, "changes": changes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="repair_conversations",
        description=(
            "Re-read Claude Code JSONL files referenced via ingestion_log "
            "and repair conversations.project_path (cwd-based, not "
            "hash-mangled) and token_count_in / token_count_out (which "
            "were never populated by the original ingest)."
        ),
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes; do not write to the DB.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap on number of files processed (smoke testing).")
    args = ap.parse_args(argv)

    try:
        conn = psycopg2.connect(**db_config())
    except Exception as e:
        print(f"[repair] DB connect failed: {e}", file=sys.stderr)
        return 1

    try:
        # Group ingestion_log files by session_id (extracted from JSONL).
        # The same session_id can span many files: the parent transcript
        # plus one subagent file per dispatched subagent. Aggregating
        # across the whole group is the only way to land the correct
        # token totals.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path FROM public.ingestion_log "
                "WHERE file_path LIKE %s ORDER BY ingested_at",
                ("%.jsonl",),
            )
            paths = [r[0] for r in cur.fetchall()]

        if args.limit:
            paths = paths[: args.limit]

        print(f"[repair] {len(paths)} JSONL file(s) to inspect")

        # Build session_id → [paths] map.
        by_session: dict[str, list[str]] = {}
        unreadable = 0
        for fp in paths:
            _c, _i, _o, sid = parse_jsonl(Path(fp))
            if not sid:
                unreadable += 1
                continue
            by_session.setdefault(sid, []).append(fp)

        print(f"[repair] grouped into {len(by_session)} session(s); "
              f"{unreadable} file(s) unreadable / no session id")

        n_updated = 0
        n_would = 0
        n_nochange = 0
        n_skipped = 0
        sample: list[dict] = []
        for sid, files in by_session.items():
            res = repair_session(conn, session_id=sid, file_paths=files, dry_run=args.dry_run)
            s = res["status"]
            if s == "updated":
                n_updated += 1
            elif s == "would-update":
                n_would += 1
                if len(sample) < 5:
                    sample.append(res)
            elif s == "no-change":
                n_nochange += 1
            else:
                n_skipped += 1

        print(f"[repair] no-change:    {n_nochange}")
        print(f"[repair] skipped:      {n_skipped}")
        if args.dry_run:
            print(f"[repair] would update: {n_would}")
            for s in sample:
                print(f"  conv #{s['id']}:")
                for col, (old, new) in s["changes"].items():
                    print(f"    {col}: {old!r} → {new!r}")
            if n_would > len(sample):
                print(f"  … (and {n_would - len(sample)} more)")
        else:
            print(f"[repair] updated:      {n_updated}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
