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

import json
import os
from pathlib import Path
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


#: Suffix for the row counts that travel beside an archive.
COUNTS_SUFFIX = ".counts.json"


def write_counts_beside(dump: Path | str, counts: dict[str, int]) -> Path:
    """Record the row counts an archive should reproduce, beside the archive.

    A dump carried to another machine cannot be compared against its source:
    the source is not reachable from there, which is the whole reason the dump
    exists. The counts travel with it so the restore can say whether it
    reproduced the corpus or half of it.
    """
    sidecar = Path(str(dump) + COUNTS_SUFFIX)
    sidecar.write_text(json.dumps(counts, indent=1, sort_keys=True), encoding="utf-8")
    return sidecar


def read_counts_beside(dump: Path | str) -> dict[str, int] | None:
    """The counts recorded beside an archive, or None if there are none.

    A missing or unreadable sidecar reads as "nothing to check against" rather
    than as an error: saying so is honest, comparing against junk is not.
    """
    try:
        payload = json.loads(Path(str(dump) + COUNTS_SUFFIX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return {str(k): int(v) for k, v in payload.items()}


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
    parser.add_argument("--target-url", default=None, help="Target connection URL. Its contents are replaced.")
    parser.add_argument("--dump-file", default=None, help="Where to keep the archive (default: a temporary file).")
    parser.add_argument(
        "--export-to",
        default=None,
        help="Write an archive plus its row counts and stop. For carrying a corpus to another machine.",
    )
    parser.add_argument(
        "--from-dump",
        default=None,
        help="Restore an archive written by --export-to into the target, then verify it against its counts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report the plan and the counts; move nothing.")
    args = parser.parse_args(argv)

    if args.export_to and args.from_dump:
        print("ERROR: --export-to writes an archive, --from-dump reads one. Pick one.", file=sys.stderr)
        return 2
    if not args.export_to and not args.target_url:
        print("ERROR: --target-url is required unless --export-to is given.", file=sys.stderr)
        return 2

    if args.from_dump:
        return _load_archive(args.from_dump, args.target_url)

    source_url = args.source_url or _default_source_url()

    if args.export_to:
        return _write_archive(source_url, args.export_to)

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


def _write_archive(source_url: str, out: str) -> int:
    """Dump the source and record what the dump should reproduce."""
    import subprocess
    import sys

    version = _version(source_url)
    if version is None:
        print(f"ERROR: cannot reach the source: {source_url}", file=sys.stderr)
        return 2

    counts = _counts(source_url)
    problems = preflight(source_version=version, target_version=version, source_counts=counts)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 2

    argv, extra = dump_command(source_url, out)
    proc = subprocess.run(argv, env={**os.environ, **extra}, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        return 1

    sidecar = write_counts_beside(out, counts)
    size = Path(out).stat().st_size
    print(f"archive  {out}  {size / 1_048_576:.0f} MB")
    print(f"counts   {sidecar}  {sum(counts.values()):,} rows across {len(counts)} tables")
    print("\nCarry both files. The restore checks one against the other.")
    return 0


def _load_archive(dump: str, target_url: str) -> int:
    """Replace the target with an archive, then check it reproduced the counts."""
    import subprocess
    import sys

    if not Path(dump).is_file():
        print(f"ERROR: no such archive: {dump}", file=sys.stderr)
        return 2

    expected = read_counts_beside(dump)
    if expected is None:
        print(
            f"ERROR: no counts beside {dump}. Without them a half-restored corpus "
            "cannot be told from a whole one — copy the .counts.json too.",
            file=sys.stderr,
        )
        return 2

    if _version(target_url) is None:
        print(f"ERROR: cannot reach the target: {target_url}", file=sys.stderr)
        return 2

    print(f"restoring {sum(expected.values()):,} rows across {len(expected)} tables into the target")

    conn = _connect(target_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for statement in reset_statements():
                cur.execute(statement)
    finally:
        conn.close()

    argv, extra = restore_command(target_url, dump)
    proc = subprocess.run(argv, env={**os.environ, **extra}, capture_output=True, text=True)
    if proc.stderr.strip():
        print(f"    {proc.stderr.strip().splitlines()[-1][:160]}")

    gaps = count_gaps(expected, _counts(target_url))
    if gaps:
        print("\nThe restore did not reproduce the archive:", file=sys.stderr)
        for gap in gaps:
            print(f"  {gap}", file=sys.stderr)
        return 1

    print(f"\nAll {len(expected)} tables match the archive.")
    return 0
