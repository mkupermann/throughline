"""Applying a migration to both machines of a replicating pair.

Logical replication carries rows, never DDL. Add a column on one node and the
other's subscription stops at the first row that has it — quietly, with the
slot growing behind it. Doing it by hand means remembering the second machine
at the moment you are least likely to: right after the first one worked.
"""

from __future__ import annotations

import pytest

from throughline.jobs.migrate_peer import migration_plan, subscription_statements


def test_replication_is_paused_before_any_schema_changes():
    """Order is the whole problem.

    Both nodes publish and subscribe, so between "applied here" and "applied
    there" either one can send a row the other cannot accept. Pausing both
    subscriptions first removes the window entirely, which is cheaper than
    reasoning about which direction is safe for which kind of change.
    """
    plan = migration_plan(
        local_pending=["006_x.sql"], peer_pending=["006_x.sql"], subscriptions=[("local", "von_peer")]
    )
    kinds = [step["kind"] for step in plan]

    assert kinds.index("disable") < kinds.index("apply")
    assert kinds.index("apply") < kinds.index("enable")


def test_both_nodes_are_migrated_before_replication_resumes():
    plan = migration_plan(
        local_pending=["006_x.sql"], peer_pending=["006_x.sql"], subscriptions=[("local", "von_peer")]
    )
    applies = [s for s in plan if s["kind"] == "apply"]
    enables = [s for s in plan if s["kind"] == "enable"]

    assert {s["node"] for s in applies} == {"local", "peer"}
    assert plan.index(applies[-1]) < plan.index(enables[0])


def test_a_node_that_is_already_current_is_not_migrated_again():
    plan = migration_plan(local_pending=[], peer_pending=["006_x.sql"], subscriptions=[("local", "von_peer")])
    assert [s["node"] for s in plan if s["kind"] == "apply"] == ["peer"]


def test_nothing_pending_anywhere_means_nothing_happens():
    # Not even a pause: stopping replication to do nothing is a small outage
    # for no reason.
    assert migration_plan(local_pending=[], peer_pending=[], subscriptions=[("local", "von_peer")]) == []


def test_every_subscription_is_paused_not_just_the_first():
    plan = migration_plan(local_pending=["006_x.sql"], peer_pending=[], subscriptions=[("local", "a"), ("peer", "b")])
    disabled = [(s["node"], s["subscription"]) for s in plan if s["kind"] == "disable"]
    enabled = [(s["node"], s["subscription"]) for s in plan if s["kind"] == "enable"]
    # Both nodes subscribe to each other, so pausing one side leaves the other
    # free to send a row the paused side has not got a column for.
    assert disabled == [("local", "a"), ("peer", "b")]
    assert sorted(enabled) == [("local", "a"), ("peer", "b")]


def test_the_statements_name_the_subscription():
    off, on = subscription_statements("von_framework")
    assert off == "ALTER SUBSCRIPTION von_framework DISABLE"
    assert on == "ALTER SUBSCRIPTION von_framework ENABLE"


@pytest.mark.parametrize("name", ["a b", "a;drop", 'a"b', ""])
def test_a_subscription_name_that_is_not_an_identifier_is_refused(name):
    # The name reaches a statement that cannot be parameterised.
    with pytest.raises(ValueError):
        subscription_statements(name)


# --------------------------------------------------------------------------- #
# What must never be replicated                                               #
# --------------------------------------------------------------------------- #


def test_the_migration_ledger_is_not_published():
    """`applied_migrations` is per-node bookkeeping, not shared data.

    Publishing it deadlocks the pair the first time both nodes apply the same
    migration: each inserts the same primary key locally, then tries to send it
    to the other, and the subscription dies on a duplicate key — every five
    seconds, forever, while the slot grows. Observed exactly that.
    """
    from throughline.jobs.migrate_peer import NEVER_REPLICATED, publication_statement

    assert "applied_migrations" in NEVER_REPLICATED
    sql = publication_statement("throughline_all", ["conversations", "messages", "applied_migrations"])
    assert "applied_migrations" not in sql
    assert "conversations" in sql and "messages" in sql


def test_the_publication_names_tables_explicitly():
    # FOR ALL TABLES cannot exclude anything, which is how the ledger got in.
    from throughline.jobs.migrate_peer import publication_statement

    sql = publication_statement("p", ["conversations"])
    assert "FOR ALL TABLES" not in sql.upper()
    assert "FOR TABLE" in sql.upper()


def test_a_publication_of_nothing_is_refused():
    from throughline.jobs.migrate_peer import publication_statement

    with pytest.raises(ValueError):
        publication_statement("p", ["applied_migrations"])
