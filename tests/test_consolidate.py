"""Moving the corpus into the Compose database.

A one-way move of 599 MB that must not lose a row. Everything decidable
without touching a database is decided here, because the parts that do touch
one get exactly one chance.
"""

from __future__ import annotations

import pytest

from throughline.jobs.consolidate import count_gaps, preflight


def test_matching_counts_are_no_gap():
    counts = {"conversations": 3883, "messages": 103700}
    assert count_gaps(counts, dict(counts)) == []


def test_a_table_that_lost_rows_is_reported_with_both_numbers():
    gaps = count_gaps({"messages": 103700}, {"messages": 103699})
    assert len(gaps) == 1
    assert "messages" in gaps[0] and "103700" in gaps[0] and "103699" in gaps[0]


def test_a_table_missing_entirely_from_the_target_is_a_gap():
    assert count_gaps({"skills": 310}, {}) != []


def test_extra_rows_in_the_target_are_reported_too():
    # The target held a smaller corpus of its own. Rows left over from it mean
    # the load did not replace what it was supposed to replace.
    gaps = count_gaps({"conversations": 3883}, {"conversations": 4645})
    assert gaps and "4645" in gaps[0]


def test_preflight_refuses_a_major_version_mismatch():
    problems = preflight(source_version="16.14", target_version="15.6", source_counts={"conversations": 1})
    assert any("16" in p and "15" in p for p in problems)


def test_preflight_accepts_a_minor_version_difference():
    problems = preflight(source_version="16.14", target_version="16.13", source_counts={"conversations": 1})
    assert problems == []


def test_preflight_refuses_an_empty_source():
    # Loading an empty dump over the target would destroy it silently.
    problems = preflight(source_version="16.14", target_version="16.13", source_counts={"conversations": 0})
    assert any("leer" in p.lower() or "empty" in p.lower() for p in problems)


@pytest.mark.parametrize("version", ["", None, "kaputt"])
def test_preflight_refuses_a_version_it_cannot_read(version):
    problems = preflight(source_version=version, target_version="16.13", source_counts={"conversations": 1})
    assert problems


# --------------------------------------------------------------------------- #
# The commands that actually move the rows                                    #
# --------------------------------------------------------------------------- #


def test_the_dump_is_portable_between_owners():
    from throughline.jobs.consolidate import dump_command

    argv, _ = dump_command("postgresql://me@localhost:5432/claude_memory", "/tmp/corpus.dump")

    assert argv[0] == "pg_dump"
    assert "--format=custom" in argv
    # The target is owned by a different role in a different container.
    assert "--no-owner" in argv and "--no-acl" in argv
    assert "/tmp/corpus.dump" in argv


def test_the_restore_replaces_what_is_there():
    from throughline.jobs.consolidate import restore_command

    argv, _ = restore_command("postgresql://u@127.0.0.1:5433/throughline", "/tmp/corpus.dump")

    assert argv[0] == "pg_restore"
    assert "--clean" in argv and "--if-exists" in argv
    assert "--no-owner" in argv and "--no-acl" in argv


def test_a_password_never_reaches_the_command_line():
    # argv is visible to every process on the machine via ps.
    from throughline.jobs.consolidate import restore_command

    argv, env = restore_command("postgresql://u:hunter2@127.0.0.1:5433/throughline", "/tmp/x.dump")

    assert not any("hunter2" in part for part in argv)
    assert env.get("PGPASSWORD") == "hunter2"


def test_a_url_without_a_password_sets_no_variable():
    from throughline.jobs.consolidate import restore_command

    _, env = restore_command("postgresql://u@127.0.0.1:5433/throughline", "/tmp/x.dump")
    assert "PGPASSWORD" not in env


def test_the_source_is_never_given_a_destructive_flag():
    # The source is the fallback until the counts agree. Nothing in the dump
    # path may modify it.
    from throughline.jobs.consolidate import dump_command

    argv, _ = dump_command("postgresql://me@localhost:5432/claude_memory", "/tmp/x.dump")
    for flag in ("--clean", "--create", "--if-exists"):
        assert flag not in argv


def test_the_target_schema_is_emptied_before_the_load():
    """pg_restore --clean is not enough on a schema with foreign keys.

    `DROP TABLE conversations` fails while messages, embeddings and the rest
    reference it; pg_restore reports the failure as an ignored error and the
    COPY that follows *appends*. Measured: a target holding 762 conversations
    ended up with 4,645 after loading a source of 3,883, while every table
    nothing referenced was replaced correctly — a mismatch in exactly the one
    table that matters most.
    """
    from throughline.jobs.consolidate import reset_statements

    statements = reset_statements()
    joined = " ".join(statements).lower()
    assert "drop schema" in joined
    assert "cascade" in joined
    assert "create schema" in joined


def test_the_reset_recreates_the_schema_it_drops():
    from throughline.jobs.consolidate import reset_statements

    dropped = [s for s in reset_statements() if s.lower().startswith("drop schema")]
    created = [s for s in reset_statements() if s.lower().startswith("create schema")]
    assert len(dropped) == 1 and len(created) == 1
    assert "public" in dropped[0] and "public" in created[0]
