"""Time-series and calendar-event queries.

Every cutoff is passed as a bound parameter. The GUI version interpolated the
timestamp straight into the SQL text (``AND created_at >= '{cutoff}'::timestamptz``),
which is both an injection shape and a plan-cache pessimisation.
"""

from __future__ import annotations

from datetime import datetime

from ._exec import Row, rows


def conversations_per_day(conn, days: int = 14) -> list[Row]:
    """Conversation counts bucketed by day, most recent *days* only.

    Days with no conversations are absent from the result — callers that need
    a dense series (a line chart) reindex against a full date range so the
    line does not interpolate across a gap.
    """
    return rows(
        conn,
        """
        SELECT date_trunc('day', started_at)::date AS day, count(*) AS n
        FROM conversations
        -- Sessions a person had. Without this the chart counted the tool's own
        -- `claude -p` calls: 523 conversations over 30 days against 120 real
        -- ones, so the curve was mostly a record of Throughline running.
        WHERE generated_by IS NULL
          AND started_at >= now() - make_interval(days => %s)
        GROUP BY day
        ORDER BY day
        """,
        (days,),
    )


def messages_per_day(conn, days: int = 14) -> list[Row]:
    return rows(
        conn,
        """
        SELECT date_trunc('day', created_at)::date AS day, count(*) AS n
        FROM messages
        WHERE created_at >= now() - make_interval(days => %s)
        GROUP BY day
        ORDER BY day
        """,
        (days,),
    )


def chunks_per_day(conn, days: int = 14) -> list[Row]:
    return rows(
        conn,
        """
        SELECT date_trunc('day', created_at)::date AS day, count(*) AS n
        FROM memory_chunks
        WHERE created_at >= now() - make_interval(days => %s)
        GROUP BY day
        ORDER BY day
        """,
        (days,),
    )


# ── Calendar event sources ───────────────────────────────────────────────────
# Each returns rows carrying an `event_date` so the timeline view can merge
# them into one stream. `cutoff` of None means "no lower bound".

def events_conversations(conn, cutoff: datetime | None = None) -> list[Row]:
    return rows(
        conn,
        """
        SELECT c.id, c.summary, c.project_name, c.model, c.message_count,
               c.started_at AS event_date, c.ended_at
        FROM conversations c
        -- The event stream a reader browses; same rule as every other listing.
        WHERE c.generated_by IS NULL
          AND c.started_at IS NOT NULL
          AND (%s::timestamptz IS NULL OR c.started_at >= %s::timestamptz)
        ORDER BY c.started_at
        """,
        (cutoff, cutoff),
    )


def events_memory(
    conn,
    cutoff: datetime | None = None,
    categories: list[str] | None = None,
    limit: int = 1000,
) -> list[Row]:
    """Memory chunks as timeline events.

    A chunk extracted from a conversation is dated by that conversation's
    start, not by when extraction happened to run — otherwise a re-extraction
    would move months of history onto today. ``mc.created_at`` is the
    fallback for chunks with no conversation behind them (manual entries,
    imports).

    ``categories`` of ``None`` or ``[]`` means "all", matching the GUI's
    "empty = all" multiselect.
    """
    return rows(
        conn,
        """
        SELECT mc.id, mc.content, mc.category::text AS category, mc.confidence,
               mc.project_name,
               COALESCE(c.started_at, mc.created_at) AS event_date,
               mc.source_type, mc.source_id
        FROM memory_chunks mc
        LEFT JOIN conversations c
          ON mc.source_type = 'conversation' AND mc.source_id = c.id
        WHERE COALESCE(c.started_at, mc.created_at) IS NOT NULL
          AND COALESCE(mc.status, 'active') = 'active'
          AND (%s::timestamptz IS NULL
               OR COALESCE(c.started_at, mc.created_at) >= %s::timestamptz)
          AND (%s::text[] IS NULL OR mc.category::text = ANY(%s::text[]))
        ORDER BY event_date
        LIMIT %s
        """,
        (cutoff, cutoff, categories or None, categories or None, limit),
    )


def events_skills(conn, cutoff: datetime | None = None) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, name, description, use_count,
               COALESCE(file_modified, last_used, created_at) AS event_date,
               CASE WHEN last_used IS NOT NULL     THEN 'used'
                    WHEN file_modified IS NOT NULL THEN 'file'
                    ELSE 'scanned'
               END AS src_type
        FROM skills
        WHERE COALESCE(file_modified, last_used, created_at) IS NOT NULL
          AND (%s::timestamptz IS NULL
               OR COALESCE(file_modified, last_used, created_at) >= %s::timestamptz)
        ORDER BY event_date
        """,
        (cutoff, cutoff),
    )


def events_projects(conn, cutoff: datetime | None = None) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, name, description, status::text AS status, created_at AS event_date
        FROM projects
        WHERE created_at IS NOT NULL
          AND (%s::timestamptz IS NULL OR created_at >= %s::timestamptz)
        ORDER BY created_at
        """,
        (cutoff, cutoff),
    )


def events_prompts(conn, cutoff: datetime | None = None) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, name, category, created_at AS event_date
        FROM prompts
        WHERE created_at IS NOT NULL
          AND (%s::timestamptz IS NULL OR created_at >= %s::timestamptz)
        ORDER BY created_at
        """,
        (cutoff, cutoff),
    )


def events_entities(conn, cutoff: datetime | None = None, limit: int = 300) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, name, entity_type, mention_count, first_seen AS event_date
        FROM entities
        WHERE first_seen IS NOT NULL
          AND (%s::timestamptz IS NULL OR first_seen >= %s::timestamptz)
        ORDER BY mention_count DESC
        LIMIT %s
        """,
        (cutoff, cutoff, limit),
    )


def events_reflections(conn, cutoff: datetime | None = None) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, reflection_type, action_taken, reasoning, created_at AS event_date
        FROM memory_reflections
        WHERE created_at IS NOT NULL
          AND (%s::timestamptz IS NULL OR created_at >= %s::timestamptz)
        ORDER BY created_at
        """,
        (cutoff, cutoff),
    )


def events_ingestion(conn, cutoff: datetime | None = None) -> list[Row]:
    return rows(
        conn,
        """
        SELECT id, file_path, record_count, ingested_at AS event_date
        FROM ingestion_log
        WHERE ingested_at IS NOT NULL
          AND (%s::timestamptz IS NULL OR ingested_at >= %s::timestamptz)
        ORDER BY ingested_at
        """,
        (cutoff, cutoff),
    )


#: Timeline source name -> query function. The GUI's eight toggles and the
#: API's `sources=` parameter both iterate this.
EVENT_SOURCES = {
    "conversations": events_conversations,
    "memory": events_memory,
    "skills": events_skills,
    "projects": events_projects,
    "prompts": events_prompts,
    "entities": events_entities,
    "reflections": events_reflections,
    "ingestion": events_ingestion,
}
