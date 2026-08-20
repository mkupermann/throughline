"""Retiring the path-derived Windsurf identifiers.

Deriving `session_id` from the absolute path meant the same plan was a
different session on every machine. Fixing the derivation is not enough on a
database that already holds the old ones: the next ingest would insert the
plan again under its new identifier and leave the old row beside it, so a fix
for duplicates would produce duplicates.
"""

from __future__ import annotations

import uuid

from throughline.adapters.windsurf import _NS
from throughline.jobs.repair_conversations import legacy_windsurf_id, plan_id_repairs


def _new(name: str) -> str:
    return str(uuid.uuid5(_NS, f"windsurf:{name}"))


def test_the_legacy_identifier_is_reproducible_from_the_path(tmp_path):
    path = tmp_path / "plans" / "auth.md"
    assert legacy_windsurf_id(path) == str(uuid.uuid5(uuid.NAMESPACE_URL, str(path)))


def test_a_row_under_the_old_identifier_is_renamed_not_deleted(tmp_path):
    # The old row carries its extracted memory and embeddings. Deleting it and
    # letting the ingest rebuild would throw those away and cost a model run.
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "auth.md"
    plan.touch()

    repairs = plan_id_repairs([plan], existing={legacy_windsurf_id(plan)})

    assert repairs == [(legacy_windsurf_id(plan), _new("auth.md"))]


def test_a_plan_already_stored_under_the_new_identifier_is_left_alone(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "auth.md"
    plan.touch()

    assert plan_id_repairs([plan], existing={_new("auth.md")}) == []


def test_nothing_is_renamed_onto_an_identifier_that_is_already_taken(tmp_path):
    # Renaming onto an existing session_id violates the unique constraint and
    # would abort the whole repair.
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "auth.md"
    plan.touch()

    both = {legacy_windsurf_id(plan), _new("auth.md")}
    assert plan_id_repairs([plan], existing=both) == []


def test_a_plan_the_database_has_never_seen_needs_no_repair(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    plan = plans / "neu.md"
    plan.touch()

    assert plan_id_repairs([plan], existing=set()) == []
