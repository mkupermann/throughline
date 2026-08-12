"""Timeline: a bucketed aggregate over a date range.

The previous Timeline rendered `data.items` — the current page of search
results, 30 by default. Sources were verified reachable; range never was.
This returns counts per (bucket, provider, kind), so the row count depends on
the range and the provider count, never on the corpus size: 90 days x 9
providers is ~810 rows whether the database holds 3,000 conversations or
3,000,000. Detail arrives only when a cell is clicked.
"""

from __future__ import annotations

from datetime import date

from ._exec import rows

#: Skills, projects, prompts, entities, reflections and ingestion runs are not
#: per-tool. They get their own lane rather than being forced into a provider
#: or dropped, so all eight of the old Calendar's sources
#: (throughline/queries/activity.py's EVENT_SOURCES) stay reachable — plus
#: `message`, which the old Calendar did not break out on its own.
NOT_TOOL_SPECIFIC = "not_tool_specific"

#: The lane label `aggregate()`/`day_detail()` emit for rows whose provider
#: column is NULL (`COALESCE(source_tool, 'unattributed')` below). Not a
#: value `source_tool` can ever hold — real unattributed rows are NULL, never
#: the literal string. Requesting this as a provider filter therefore has to
#: mean "also match NULL", not "match the literal string" (which `= ANY(...)`
#: alone can never do — NULL never equals anything). See _split_providers.
UNATTRIBUTED = "unattributed"

BUCKETS = ("day", "week", "month")

#: Every kind the Timeline can show, with the table and timestamp it reads and
#: how it reaches a provider (None = the not-tool-specific lane).
_SOURCES: dict[str, tuple[str, str, str | None]] = {
    "conversation": ("conversations c", "c.started_at", "c.source_tool"),
    "message": (
        "messages m JOIN conversations c ON c.id = m.conversation_id",
        "m.created_at",
        "c.source_tool",
    ),
    "memory": (
        "memory_chunks mc LEFT JOIN conversations c "
        "ON mc.source_type = 'conversation' AND mc.source_id = c.id",
        "mc.created_at",
        "c.source_tool",
    ),
    # Column names verified against throughline/queries/activity.py, which
    # already reads all six tables. `skills` has no single event timestamp —
    # activity.py coalesces the same three columns, and so must this.
    "skill": ("skills s", "COALESCE(s.file_modified, s.last_used, s.created_at)", None),
    "project": ("projects p", "p.created_at", None),
    "prompt": ("prompts pr", "pr.created_at", None),
    "entity": ("entities e", "e.first_seen", None),
    "reflection": ("memory_reflections mr", "mr.created_at", None),
    # File paths could in principle be matched back to an adapter, but that's
    # a path-parsing heuristic for no real benefit — ingestion is a pipeline
    # concern, not a per-tool one. Leave it in the not-tool-specific lane.
    "ingestion": ("ingestion_log il", "il.ingested_at", None),
}


def _split_providers(providers: list[str]) -> tuple[list[str], bool]:
    """Split a requested provider list into (named providers, include_null).

    `"unattributed"` is the sentinel the client sends for "rows with no
    recorded tool" (see UNATTRIBUTED) — it never appears in `source_tool`
    itself, so it is pulled out of the list that becomes `= ANY(...)` and
    turned into an explicit `IS NULL` instead.
    """
    named = [p for p in providers if p != UNATTRIBUTED]
    return named, len(named) != len(providers)


#: Kinds that reach a `conversations` row, and can therefore be limited to
#: sessions a person had. Conversations and messages join it directly; a memory
#: chunk joins it only when it was extracted from one, so its filter has to
#: allow the LEFT JOIN's NULL through — a chunk written by hand belongs on the
#: timeline as much as one distilled from a transcript.
_HUMAN_FILTER: dict[str, str] = {
    "conversation": "AND c.generated_by IS NULL",
    "message": "AND c.generated_by IS NULL",
    "memory": "AND (c.id IS NULL OR c.generated_by IS NULL)",
}


def _provider_filter(
    provider_col: str, providers: list[str], named: list[str], include_null: bool
) -> str:
    """The `AND ...` fragment for one kind's provider column.

    Three shapes, depending on what was requested:
    - nothing requested -> no filter.
    - only "unattributed" -> `IS NULL` alone (an empty `= ANY(%(providers)s)`
      with no bound param would be a SQL error, not "match nothing").
    - anything else -> `= ANY(...)`, OR'd with `IS NULL` when "unattributed"
      was requested alongside named providers, so a mixed request like
      ["hermes", "unattributed"] returns the union rather than silently
      picking one.
    """
    if not providers:
        return ""
    if include_null and not named:
        return f" AND {provider_col} IS NULL"
    if include_null:
        return f" AND ({provider_col} = ANY(%(providers)s) OR {provider_col} IS NULL)"
    return f" AND {provider_col} = ANY(%(providers)s)"


def pick_bucket(since: date, until: date) -> str:
    """<=90 days by day, <=2 years by week, beyond by month.

    Keeps "all time" cheap without the caller having to think about it.
    """
    span = (until - since).days
    if span <= 90:
        return "day"
    if span <= 730:
        return "week"
    return "month"


def aggregate(
    conn,
    since: date,
    until: date,
    bucket: str,
    kinds: list[str],
    providers: list[str],
) -> list[dict]:
    if bucket not in BUCKETS:
        raise ValueError(f"bucket must be one of {BUCKETS}, got {bucket!r}")
    wanted = [k for k in (kinds or list(_SOURCES)) if k in _SOURCES]
    if not wanted:
        return []

    params: dict = {"since": since, "until": until, "bucket": bucket}
    named_providers, include_null = _split_providers(providers)
    if named_providers:
        params["providers"] = named_providers

    parts: list[str] = []
    for kind in wanted:
        frm, ts, provider_col = _SOURCES[kind]
        # Machine-generated sessions are not activity a person recognises: the
        # tool's own `claude -p` calls outnumbered real ones ten to one, so the
        # grid was largely a record of Throughline running.
        human_filter = _HUMAN_FILTER.get(kind, "")
        if provider_col is None:
            if providers:
                # A provider scope is active and this kind has no provider.
                continue
            provider_expr = f"'{NOT_TOOL_SPECIFIC}'"
            provider_filter = ""
        else:
            provider_expr = f"COALESCE({provider_col}, '{UNATTRIBUTED}')"
            provider_filter = _provider_filter(provider_col, providers, named_providers, include_null)

        parts.append(
            f"""
            SELECT date_trunc(%(bucket)s, {ts})::date AS bucket,
                   {provider_expr} AS provider,
                   '{kind}' AS kind,
                   count(*) AS n
            FROM {frm}
            WHERE {ts} >= %(since)s
              AND {ts} < (%(until)s::date + interval '1 day')
              {provider_filter}
              {human_filter}
            GROUP BY 1, 2
            """
        )

    if not parts:
        return []
    sql = " UNION ALL ".join(parts) + " ORDER BY bucket, provider, kind"
    return rows(conn, sql, params)


def day_detail(
    conn,
    day: date,
    kinds: list[str],
    providers: list[str],
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """One day's events. Clicking a cell is what loads rows."""
    # Default must match aggregate()'s: a cell counted with no kind filter
    # (all nine kinds) has to expand into a list of the same nine kinds, or
    # the number you click and the list you get disagree.
    wanted = [k for k in (kinds or list(_SOURCES)) if k in _SOURCES]
    if not wanted:
        return []

    params: dict = {"day": day, "limit": limit, "offset": offset}
    named_providers, include_null = _split_providers(providers)
    if named_providers:
        params["providers"] = named_providers

    parts: list[str] = []
    for kind in wanted:
        frm, ts, provider_col = _SOURCES[kind]
        id_expr, title_expr, conv_expr = _detail_columns(kind)
        # Same rule as the grid above: a cell that counts N events must open to
        # the same N, or the number and the list disagree.
        human_filter = _HUMAN_FILTER.get(kind, "")
        if provider_col is None:
            if providers:
                continue
            provider_expr = f"'{NOT_TOOL_SPECIFIC}'"
            provider_filter = ""
        else:
            provider_expr = f"COALESCE({provider_col}, '{UNATTRIBUTED}')"
            provider_filter = _provider_filter(provider_col, providers, named_providers, include_null)

        parts.append(
            f"""
            SELECT {id_expr} AS id,
                   '{kind}' AS kind,
                   {provider_expr} AS provider,
                   {ts} AS ts,
                   {title_expr} AS title,
                   {conv_expr} AS conversation_id
            FROM {frm}
            WHERE {ts} >= %(day)s
              AND {ts} < (%(day)s::date + interval '1 day')
              {human_filter}
              {provider_filter}
            """
        )

    if not parts:
        return []
    # Containers before their contents.
    #
    # Ordering by time alone let messages fill the page: a busy day holds 1,678
    # messages and 20 conversations, so the first 100 rows were 99 messages and
    # one memory chunk, and the 20 conversations the reader clicked the cell to
    # find were not among them. The panel said "showing 100 of 1,678" — honest,
    # and still the wrong hundred.
    #
    # A conversation is what a person recognises; a message is a line inside
    # one. Skills, projects and prompts rank with conversations because they are
    # also things rather than fragments.
    #
    # Wrapped in a subquery because Postgres allows only result column names in
    # an ORDER BY that follows UNION ALL, never an expression.
    sql = (
        "SELECT * FROM (\n"
        + " UNION ALL ".join(parts)
        + """
        ) u
        ORDER BY CASE u.kind
                   WHEN 'conversation' THEN 0
                   WHEN 'skill'        THEN 1
                   WHEN 'project'      THEN 1
                   WHEN 'prompt'       THEN 1
                   WHEN 'memory'       THEN 2
                   ELSE 3
                 END,
                 u.ts DESC, u.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """
    )
    return rows(conn, sql, params)


def _detail_columns(kind: str) -> tuple[str, str, str]:
    """(id, title, conversation_id) expressions per kind for the day view.

    The third element is what makes a row openable. A message row carries
    `m.id`, which is NOT a conversation id — linking a message to
    /c/<message id> would open a different conversation entirely, and
    plausibly, since both id spaces overlap. Every kind therefore states
    which conversation it belongs to, or NULL when it belongs to none.
    """
    return {
        # A conversation IS its own conversation; a message names its parent.
        # memory_chunks.source_id is a conversation id only when source_type
        # says so — the same polymorphism guarded elsewhere in this module.
        "conversation": ("c.id", "COALESCE(c.summary, c.project_name, '(conversation)')", "c.id"),
        "message": ("m.id", "left(m.content, 200)", "m.conversation_id"),
        "memory": (
            "mc.id",
            "left(mc.content, 200)",
            "CASE WHEN mc.source_type = 'conversation' THEN mc.source_id END",
        ),
        # The rest belong to no conversation and open on their own routes.
        "skill": ("s.id", "s.name", "NULL::bigint"),
        "project": ("p.id", "p.name", "NULL::bigint"),
        "prompt": ("pr.id", "COALESCE(pr.name, '(prompt)')", "NULL::bigint"),
        "entity": ("e.id", "e.name", "NULL::bigint"),
        "reflection": ("mr.id", "mr.reflection_type", "NULL::bigint"),
        "ingestion": ("il.id", "il.file_path", "NULL::bigint"),
    }[kind]
