"""The Timeline covers a range, not a page.

Bar 6 is stated at length in the spec because Timeline has failed twice by
verifying the wrong property: sources reachable rather than range complete,
page rendered rather than data whole. These tests reconcile lane totals
against raw counts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from throughline.queries import timeline as T

pytestmark = pytest.mark.integration


@pytest.fixture()
def spread(db_connection):
    """300 conversations over 300 days — more than any page limit."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db_connection.cursor() as cur:
        for i in range(300):
            cur.execute(
                "INSERT INTO conversations "
                "(session_id, project_path, source_tool, started_at, message_count) "
                "VALUES (gen_random_uuid(), '/t', %s, %s, 1)",
                ("claude_code" if i % 2 else "hermes", base + timedelta(days=i)),
            )
    db_connection.commit()
    return db_connection


def test_bucket_auto_selection():
    d = date(2026, 1, 1)
    assert T.pick_bucket(d, d + timedelta(days=30)) == "day"
    assert T.pick_bucket(d, d + timedelta(days=200)) == "week"
    assert T.pick_bucket(d, d + timedelta(days=1000)) == "month"


def test_range_is_complete_not_paginated(spread):
    """Bar 6: a date range with no query shows every conversation in it."""
    since, until = date(2026, 1, 1), date(2026, 12, 31)
    agg = T.aggregate(spread, since, until, "day", kinds=["conversation"], providers=[])
    total = sum(r["n"] for r in agg)

    with spread.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM conversations "
            "WHERE started_at >= %s AND started_at < %s + interval '1 day'",
            (since, until),
        )
        raw = cur.fetchone()[0]

    assert total == raw, f"lane totals {total} != raw count {raw}"
    assert total >= 300


def test_lanes_are_per_provider(spread):
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 12, 31), "month",
        kinds=["conversation"], providers=[],
    )
    assert {r["provider"] for r in agg} >= {"claude_code", "hermes"}


def test_provider_filter_narrows_and_still_reconciles(spread):
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 12, 31), "day",
        kinds=["conversation"], providers=["hermes"],
    )
    total = sum(r["n"] for r in agg)
    with spread.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations WHERE source_tool='hermes'")
        assert total == cur.fetchone()[0]


def test_row_count_stays_bounded_regardless_of_corpus(spread):
    """90 days x 9 providers is ~810 rows whatever the corpus size."""
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 3, 31), "day",
        kinds=["conversation"], providers=[],
    )
    assert len(agg) < 1000


def test_non_provider_sources_get_their_own_lane(db_connection):
    """§5.3: skills etc. are not per-tool and must not be dropped or forced."""
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO skills (name, description, path, created_at) "
            "VALUES ('t-skill', 'd', '/tmp/x/SKILL.md', %s) ON CONFLICT DO NOTHING",
            (datetime(2026, 5, 5, tzinfo=timezone.utc),),
        )
    db_connection.commit()
    agg = T.aggregate(
        db_connection, date(2026, 5, 1), date(2026, 5, 31), "day",
        kinds=["skill"], providers=[],
    )
    assert any(r["provider"] == T.NOT_TOOL_SPECIFIC for r in agg)


def test_month_boundary_buckets_do_not_leak(spread):
    agg = T.aggregate(
        spread, date(2026, 1, 1), date(2026, 1, 31), "month",
        kinds=["conversation"], providers=[],
    )
    assert {str(r["bucket"])[:7] for r in agg} == {"2026-01"}


def test_day_detail_returns_that_days_events(spread):
    detail = T.day_detail(
        spread, date(2026, 2, 1), kinds=["conversation"], providers=[], limit=50, offset=0
    )
    assert len(detail) >= 1
    assert all(str(r["ts"])[:10] == "2026-02-01" for r in detail)
