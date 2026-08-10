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


def test_day_detail_defaults_to_all_kinds_like_aggregate(db_connection):
    """day_detail's default kind list must match aggregate's default (every
    kind in _SOURCES), not just ['conversation'] — otherwise the number a
    cell counts (aggregate, kinds=[]) and the list clicking it shows
    (day_detail, kinds=[]) disagree."""
    day = date(2026, 7, 1)
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations "
            "(session_id, project_path, source_tool, started_at, message_count) "
            "VALUES (gen_random_uuid(), '/t', 'claude_code', %s, 1)",
            (datetime(2026, 7, 1, 12, tzinfo=timezone.utc),),
        )
        cur.execute(
            "INSERT INTO skills (name, description, path, created_at) "
            "VALUES ('t-skill-day', 'd', '/tmp/y/SKILL.md', %s) ON CONFLICT DO NOTHING",
            (datetime(2026, 7, 1, 13, tzinfo=timezone.utc),),
        )
    db_connection.commit()

    agg_kinds = {
        r["kind"] for r in T.aggregate(db_connection, day, day, "day", kinds=[], providers=[])
    }
    detail_kinds = {
        r["kind"]
        for r in T.day_detail(db_connection, day, kinds=[], providers=[], limit=100, offset=0)
    }
    assert detail_kinds == agg_kinds
    assert detail_kinds >= {"conversation", "skill"}


def test_sources_cover_every_old_calendar_source():
    """The old Calendar (throughline/queries/activity.py's EVENT_SOURCES) read
    eight sources: conversations, memory, skills, projects, prompts, entities,
    reflections, ingestion. Round 1 of this task shipped only five of the
    eight non-conversation ones — dropping entity/reflection/ingestion
    reproduced the exact "lost the complete picture" bug this task exists to
    fix. This set is written out by hand, not derived from `_SOURCES` itself,
    so a future edit that silently deletes a key still fails this test.
    """
    old_calendar_sources = {
        "conversation", "memory", "skill", "project", "prompt",
        "entity", "reflection", "ingestion",
    }
    assert old_calendar_sources <= set(T._SOURCES)


@pytest.fixture()
def calendar_extras(db_connection):
    """Rows for the three not-tool-specific sources dropped in round 1:
    entities, memory_reflections, ingestion_log."""
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with db_connection.cursor() as cur:
        for i in range(5):
            ts = base + timedelta(days=i)
            cur.execute(
                "INSERT INTO entities (entity_type, name, canonical_name, first_seen) "
                "VALUES ('person', %s, %s, %s)",
                (f"person-{i}", f"person-{i}", ts),
            )
            cur.execute(
                "INSERT INTO memory_reflections (reflection_type, created_at) "
                "VALUES ('merge', %s)",
                (ts,),
            )
            cur.execute(
                "INSERT INTO ingestion_log (file_path, file_hash, ingested_at) "
                "VALUES (%s, %s, %s)",
                (f"/x/{i}.jsonl", f"hash-{i}", ts),
            )
    db_connection.commit()
    return db_connection


@pytest.mark.parametrize(
    "kind,table,ts_col",
    [
        ("entity", "entities", "first_seen"),
        ("reflection", "memory_reflections", "created_at"),
        ("ingestion", "ingestion_log", "ingested_at"),
    ],
)
def test_calendar_extras_reconcile_and_land_in_not_tool_specific(
    calendar_extras, kind, table, ts_col
):
    """§5.3: entities/reflections/ingestion have no provider dimension. They
    must reconcile like every other lane, and land in NOT_TOOL_SPECIFIC."""
    since, until = date(2026, 6, 1), date(2026, 6, 30)
    agg = T.aggregate(calendar_extras, since, until, "day", kinds=[kind], providers=[])
    total = sum(r["n"] for r in agg)

    with calendar_extras.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {table} "
            f"WHERE {ts_col} >= %s AND {ts_col} < %s + interval '1 day'",
            (since, until),
        )
        raw = cur.fetchone()[0]

    assert total == raw == 5, f"{kind}: lane total {total} != raw count {raw}"
    assert all(r["provider"] == T.NOT_TOOL_SPECIFIC for r in agg)


def test_calendar_extras_excluded_under_active_provider_filter(calendar_extras):
    """A provider scope must not sweep in provider-less rows — same rule
    already enforced for skill/project/prompt, now covering all six
    not-tool-specific kinds."""
    agg = T.aggregate(
        calendar_extras, date(2026, 6, 1), date(2026, 6, 30), "day",
        kinds=["entity", "reflection", "ingestion"], providers=["hermes"],
    )
    assert agg == []
