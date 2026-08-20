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

from typing import Any

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
