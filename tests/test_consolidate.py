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
