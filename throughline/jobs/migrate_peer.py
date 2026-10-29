#!/usr/bin/env python3
"""Apply pending migrations to both machines of a replicating pair.

Logical replication carries rows, never DDL. Add a column on one node and the
other's subscription stops at the first row that has it — quietly, with the
slot growing behind it until it hits its retention limit. Doing this by hand
means remembering the second machine at the moment you are least likely to:
right after the first one worked.

Order is the whole problem. Both nodes publish *and* subscribe, so between
"applied here" and "applied there" either one can send a row the other cannot
accept. Pausing both subscriptions first removes that window entirely, which is
cheaper than reasoning about which direction is safe for which kind of change —
additive migrations want the subscriber first, destructive ones the publisher,
and in a bidirectional pair every node is both.

Usage::

    throughline migrate-peer --peer-url postgresql://user@127.0.0.1:5434/throughline
    throughline migrate-peer --peer-url ... --dry-run

Exit code: 0 on success, 1 if a step failed, 2 on a usage error.
"""

from __future__ import annotations

import re
from typing import Any

#: PostgreSQL identifiers this tool will interpolate into a statement.
#: ALTER SUBSCRIPTION takes no parameters, so the name is checked instead of
#: escaped — anything that is not a plain identifier is refused rather than
#: quoted and hoped for.
_IDENTIFIER = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


#: Tables that must stay local to each node.
#:
#: `applied_migrations` is per-node bookkeeping, like a replication slot's
#: position: it records what *this* database has run. Publishing it deadlocks
#: the pair the first time both nodes apply the same migration — each inserts
#: the same primary key locally, then tries to send it to the other, and the
#: subscription dies on a duplicate key every five seconds while the slot
#: grows. `FOR ALL TABLES` cannot exclude anything, which is how it got in.
NEVER_REPLICATED = frozenset({"applied_migrations"})


def publication_statement(name: str, tables: list[str]) -> str:
    """CREATE PUBLICATION over every shared table, and no others."""
    if not _IDENTIFIER.match(name or ""):
        raise ValueError(f"not a plain PostgreSQL identifier: {name!r}")
    shared = sorted(t for t in tables if t not in NEVER_REPLICATED)
    for table in shared:
        if not _IDENTIFIER.match(table):
            raise ValueError(f"not a plain PostgreSQL identifier: {table!r}")
    if not shared:
        raise ValueError("a publication with no tables replicates nothing")
    joined = ", ".join(f"public.{t}" for t in shared)
    return f"CREATE PUBLICATION {name} FOR TABLE {joined}"


def subscription_statements(name: str) -> tuple[str, str]:
    """The pause and resume statements for one subscription."""
    if not _IDENTIFIER.match(name or ""):
        raise ValueError(f"not a plain PostgreSQL identifier: {name!r}")
    return (f"ALTER SUBSCRIPTION {name} DISABLE", f"ALTER SUBSCRIPTION {name} ENABLE")


def migration_plan(
    *,
    local_pending: list[str],
    peer_pending: list[str],
    subscriptions: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """The ordered steps, or nothing at all when both nodes are current.

    Nothing pending means not even a pause: stopping replication to do nothing
    is a small outage for no reason.
    """
    if not local_pending and not peer_pending:
        return []

    plan: list[dict[str, Any]] = []
    # Both nodes subscribe to each other, so pausing one side leaves the other
    # free to send a row the paused side has no column for.
    for node, name in subscriptions:
        plan.append({"kind": "disable", "node": node, "subscription": name})
    if local_pending:
        plan.append({"kind": "apply", "node": "local", "migrations": list(local_pending)})
    if peer_pending:
        plan.append({"kind": "apply", "node": "peer", "migrations": list(peer_pending)})
    for node, name in subscriptions:
        plan.append({"kind": "enable", "node": node, "subscription": name})
    return plan


# --------------------------------------------------------------------------- #
# Running it                                                                  #
# --------------------------------------------------------------------------- #


def _pending(conn, migrations_dir=None) -> list[str]:
    """Migration files this database has not recorded as applied."""
    from throughline.jobs.migrate import MIGRATIONS_DIR, applied_set, discover_migrations, is_applied

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.applied_migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            return [p.name for p in discover_migrations(migrations_dir or MIGRATIONS_DIR)]
        applied = applied_set(cur)
    return [p.name for p in discover_migrations(migrations_dir or MIGRATIONS_DIR) if not is_applied(p, applied)]


def _subscriptions(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT subname FROM pg_subscription ORDER BY subname")
        return [r[0] for r in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import subprocess
    import sys

    import psycopg2

    parser = argparse.ArgumentParser(
        prog="throughline migrate-peer",
        description="Apply pending migrations to this database and to a replicating peer.",
    )
    parser.add_argument("--peer-url", required=True, help="Connection URL of the other node.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and change nothing.")
    args = parser.parse_args(argv)

    from throughline.config import get_db_config

    local_cfg = get_db_config()
    try:
        local = psycopg2.connect(**local_cfg)
        peer = psycopg2.connect(args.peer_url)
    except psycopg2.Error as exc:
        print(f"ERROR: cannot reach both nodes: {exc}", file=sys.stderr)
        return 2

    try:
        local.autocommit = True
        peer.autocommit = True
        conns = {"local": local, "peer": peer}

        subs = [("local", s) for s in _subscriptions(local)] + [("peer", s) for s in _subscriptions(peer)]
        plan = migration_plan(local_pending=_pending(local), peer_pending=_pending(peer), subscriptions=subs)

        if not plan:
            print("Both nodes are current. Nothing to do.")
            return 0

        for step in plan:
            if step["kind"] == "apply":
                print(f"  apply on {step['node']}: {', '.join(step['migrations'])}")
            else:
                print(f"  {step['kind']} {step['node']}/{step['subscription']}")

        if args.dry_run:
            print("\n--dry-run set; nothing was changed.")
            return 0

        # Everything after the first DISABLE has to be undone even when a
        # migration fails, or the pair is left not replicating and nobody
        # notices until the slot fills.
        disabled: list[tuple[str, str]] = []
        failure: str | None = None
        try:
            for step in plan:
                if step["kind"] == "disable":
                    off, _ = subscription_statements(step["subscription"])
                    with conns[step["node"]].cursor() as cur:
                        cur.execute(off)
                    disabled.append((step["node"], step["subscription"]))
                elif step["kind"] == "apply":
                    print(f"\n==> migrating {step['node']}")
                    code = _apply(step["node"], args.peer_url, local_cfg, subprocess)
                    if code != 0:
                        failure = f"migration failed on {step['node']}"
                        break
        finally:
            for node, name in disabled:
                _, on = subscription_statements(name)
                try:
                    with conns[node].cursor() as cur:
                        cur.execute(on)
                except Exception as exc:  # pragma: no cover - reported, not raised
                    print(f"WARNING: could not re-enable {node}/{name}: {exc}", file=sys.stderr)

        if failure:
            print(f"\nERROR: {failure}. Replication was re-enabled.", file=sys.stderr)
            return 1

        print("\nBoth nodes migrated, replication running.")
        return 0
    finally:
        local.close()
        peer.close()


def _apply(node: str, peer_url: str, local_cfg: dict, subprocess) -> int:
    """Run the packaged migration job against one node."""
    import os
    import sys

    env = dict(os.environ)
    if node == "peer":
        from throughline.jobs.consolidate import _split_password

        safe, extra = _split_password(peer_url)
        from urllib.parse import urlsplit

        parts = urlsplit(safe)
        env.update(
            {
                "PGHOST": parts.hostname or "localhost",
                "PGPORT": str(parts.port or 5432),
                "PGUSER": parts.username or "",
                "PGDATABASE": (parts.path or "/").lstrip("/"),
                **extra,
            }
        )
    return subprocess.call([sys.executable, "-m", "throughline", "migrate"], env=env)
