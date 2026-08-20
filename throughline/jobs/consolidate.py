#!/usr/bin/env python3
"""Move the corpus into another Throughline database, once.

Written for the step where one machine's native install becomes the Compose
stack that a second machine will replicate from. It is deliberately one-way
and deliberately dumb: dump the source, load it into the target, count both,
and refuse to touch the source at any point. The source is the fallback until
the counts agree.

Usage::

    throughline consolidate --target-url postgresql://user:pw@127.0.0.1:5433/throughline
    throughline consolidate --target-url ... --dry-run

Exit code: 0 on success, 1 on a failed load or a count mismatch, 2 on a
usage error or a failed preflight.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

#: Tables whose row counts must match afterwards. Derived and log tables are
#: included on purpose: a load that silently dropped embeddings would leave
#: semantic search quietly worse rather than visibly broken.
VERIFIED_TABLES = (
    "conversations",
    "messages",
    "memory_chunks",
    "embeddings",
    "entities",
    "entity_mentions",
    "relationships",
    "memory_reflections",
    "ingestion_log",
    "projects",
    "prompts",
    "skills",
)


def major(version: str | None) -> int | None:
    """The PostgreSQL major version, or None if the string is not one."""
    if not version:
        return None
    head = str(version).split(".")[0].strip()
    return int(head) if head.isdigit() else None


# --------------------------------------------------------------------------- #
# The commands                                                                #
# --------------------------------------------------------------------------- #


def _split_password(url: str) -> tuple[str, dict[str, str]]:
    """Strip the password out of a connection URL into an environment.

    argv is visible to every process on the machine through ``ps``, so a
    password belongs in PGPASSWORD and nowhere else. The URL keeps its user,
    host, port and database.
    """
    parsed = urlsplit(url)
    if not parsed.password:
        return url, {}
    netloc = parsed.hostname or ""
    if parsed.username:
        netloc = f"{parsed.username}@{netloc}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)), {
        "PGPASSWORD": parsed.password
    }


def dump_command(url: str, out: str) -> tuple[list[str], dict[str, str]]:
    """Read the source into a portable archive. Never modifies it.

    ``--no-owner`` and ``--no-acl`` because the target is owned by a different
    role in a different container; the custom format so the restore can drop
    and recreate object by object rather than replaying a script that assumes
    an empty database.
    """
    safe, env = _split_password(url)
    return ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", out, safe], env


def reset_statements() -> tuple[str, ...]:
    """Empty the target so the load cannot append to what is already there.

    ``pg_restore --clean`` is not enough on a schema with foreign keys: DROP
    TABLE conversations fails while messages, embeddings and the rest
    reference it, pg_restore reports that as an ignored error, and the COPY
    that follows appends. A target holding 762 conversations ended up with
    4,645 after loading a source of 3,883 — while every table nothing
    referenced was replaced correctly, which is the kind of partial success
    that reads as success.

    Dropping the schema is the destructive step of this whole job. It runs
    only after the preflight has confirmed the source is reachable and not
    empty.
    """
    return (
        "DROP SCHEMA IF EXISTS public CASCADE",
        "CREATE SCHEMA public",
    )


def restore_command(url: str, dump: str) -> tuple[list[str], dict[str, str]]:
    """Replace the target's contents with the archive.

    ``--clean --if-exists`` rather than loading into an empty database: the
    target already holds a corpus of its own, and leaving any of it behind
    would mean the counts agree by accident.
    """
    safe, env = _split_password(url)
    return (
        ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl", "--dbname", safe, dump],
        env,
    )


def preflight(*, source_version: str | None, target_version: str | None, source_counts: dict[str, int]) -> list[str]:
    """Everything worth refusing before a single row moves.

    A major-version mismatch is fatal because the dump would load with
    warnings and diverge in ways nobody looks for. An empty source is fatal
    because loading it over the target destroys the target for nothing — and
    that is exactly the shape of an accident where the wrong connection string
    was passed.
    """
    problems: list[str] = []

    source_major, target_major = major(source_version), major(target_version)
    if source_major is None:
        problems.append(f"cannot read the source PostgreSQL version: {source_version!r}")
    if target_major is None:
        problems.append(f"cannot read the target PostgreSQL version: {target_version!r}")
    if source_major is not None and target_major is not None and source_major != target_major:
        problems.append(
            f"PostgreSQL major versions differ: source {source_major}, target {target_major}. "
            "Match them before moving a corpus between them."
        )

    if not any(source_counts.values()):
        problems.append("the source database is empty — refusing to load nothing over the target")

    return problems


def count_gaps(source: dict[str, int], target: dict[str, int]) -> list[str]:
    """Tables whose row counts do not match, described so both numbers show.

    Extra rows in the target are reported too: the target held a smaller
    corpus of its own, and leftovers mean the load did not replace what it was
    meant to replace.
    """
    gaps: list[str] = []
    for table in sorted(source):
        want = source[table]
        got = target.get(table)
        if got is None:
            gaps.append(f"{table}: missing from the target (source has {want})")
        elif got != want:
            gaps.append(f"{table}: source {want}, target {got}")
    return gaps


def row_counts(cur: Any, tables: tuple[str, ...] = VERIFIED_TABLES) -> dict[str, int]:
    """Row count per table, skipping any the database does not have."""
    counts: dict[str, int] = {}
    for table in tables:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            continue
        cur.execute(f"SELECT count(*) FROM public.{table}")  # noqa: S608 - fixed vocabulary
        counts[table] = int(cur.fetchone()[0])
    return counts


# --------------------------------------------------------------------------- #
# Running it                                                                  #
# --------------------------------------------------------------------------- #


def _connect(url: str):
    import psycopg2

    return psycopg2.connect(url)


def _version(url: str) -> str | None:
    try:
        conn = _connect(url)
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            return str(cur.fetchone()[0])
    finally:
        conn.close()


def _counts(url: str) -> dict[str, int]:
    conn = _connect(url)
    try:
        with conn.cursor() as cur:
            return row_counts(cur)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="throughline consolidate",
        description="Move this database's contents into another Throughline database.",
    )
    parser.add_argument("--source-url", default=None, help="Source connection URL (default: the configured database).")
    parser.add_argument("--target-url", required=True, help="Target connection URL. Its contents are replaced.")
    parser.add_argument("--dump-file", default=None, help="Where to keep the archive (default: a temporary file).")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan and the counts; move nothing.")
    args = parser.parse_args(argv)

    source_url = args.source_url or _default_source_url()

    source_version, target_version = _version(source_url), _version(args.target_url)
    if source_version is None:
        print(f"ERROR: cannot reach the source: {source_url}", file=sys.stderr)
        return 2
    if target_version is None:
        print(f"ERROR: cannot reach the target: {args.target_url}", file=sys.stderr)
        return 2

    source_counts = _counts(source_url)
    target_counts = _counts(args.target_url)

    problems = preflight(source_version=source_version, target_version=target_version, source_counts=source_counts)
    if problems:
        print("Refusing to move anything:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 2

    print(
        f"source  PostgreSQL {source_version}  {sum(source_counts.values()):,} rows across {len(source_counts)} tables"
    )
    print(f"target  PostgreSQL {target_version}  {sum(target_counts.values()):,} rows — these are replaced")
    for table in sorted(source_counts):
        print(f"  {table:<20} {source_counts[table]:>9,}  ->  {target_counts.get(table, 0):>9,}")

    if args.dry_run:
        print("\n--dry-run set; nothing was moved.")
        return 0

    with tempfile.TemporaryDirectory() as scratch:
        dump = Path(args.dump_file) if args.dump_file else Path(scratch) / "corpus.dump"

        print("\n==> emptying the target")
        conn = _connect(args.target_url)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                for statement in reset_statements():
                    cur.execute(statement)
        finally:
            conn.close()

        for label, (argv_, extra) in (
            ("dump", dump_command(source_url, str(dump))),
            ("restore", restore_command(args.target_url, str(dump))),
        ):
            print(f"\n==> {label}")
            proc = subprocess.run(argv_, env={**os.environ, **extra}, capture_output=True, text=True)
            # pg_restore reports every dropped object that did not exist as an
            # error and still succeeds; the counts below are the real verdict.
            if proc.returncode != 0 and label == "dump":
                print(proc.stderr[-2000:], file=sys.stderr)
                print(f"ERROR: {label} failed", file=sys.stderr)
                return 1
            if proc.stderr.strip():
                print(f"    {proc.stderr.strip().splitlines()[-1][:160]}")

    after = _counts(args.target_url)
    gaps = count_gaps(source_counts, after)
    if gaps:
        print("\nThe move did not reproduce the source:", file=sys.stderr)
        for gap in gaps:
            print(f"  {gap}", file=sys.stderr)
        print("\nThe source is untouched. Fix the target and run again.", file=sys.stderr)
        return 1

    print(f"\nAll {len(source_counts)} tables match. The source is untouched and remains the fallback.")
    return 0


def _default_source_url() -> str:
    from throughline.config import get_db_config

    cfg = get_db_config()
    user = cfg.get("user", "")
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 5432)
    name = cfg.get("dbname", "throughline")
    auth = f"{user}:{cfg['password']}@" if cfg.get("password") else (f"{user}@" if user else "")
    return f"postgresql://{auth}{host}:{port}/{name}"


if __name__ == "__main__":
    raise SystemExit(main())
