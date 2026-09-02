"""Projects, and the sessions inside them.

A *project* here is `conversations.project_name` — the last segment of the
working directory a session ran in, computed by the schema. It is not a
manually curated label and does not always name something meaningful ("tmp"
appears on this corpus). It is, however, the only grouping that exists in the
data without asking the user to maintain one, and it matches how people
actually think about their work: the folder they were in.

Three levels, deliberately: project → session → message. The flat alternative
does not survive contact with the data — one project holds 7,284 messages and
a single session holds 5,560, so a "show me the project's history" that means
"render every message" is a page that never finishes. A project view lists its
sessions; opening a session lists its messages, which is already paginated.

Everything here excludes machine-generated conversations by default
(`generated_by IS NULL`). On the corpus this was written against that is the
difference between 330 sessions and 3,606: the tool's own `claude -p` calls
outnumbered the user's real work ten to one, which is what made every listing
in the product read as noise.
"""

from __future__ import annotations

import os
from typing import Literal

from ._exec import Row, rows

#: Working directories that are not projects.
#:
#: `project_name` is the last path segment, which is right for
#: `~/Documents/GitHub/some-repo` and wrong for a session started from nowhere
#: in particular. Measured on a real corpus: of twelve project names, nine were
#: genuine repositories and the exceptions were the home directory (listed as
#: a project named after the account) and `/tmp`.
#:
#: Recognised by PATH, not by name — a repository legitimately called `tmp`
#: keeps its identity, and the rule holds for any account without knowing the
#: user's name. This is the whole fix: no config file, no registry to maintain,
#: just refusing to claim that `cd ~` was a project.
_NOT_A_PROJECT_PATHS = frozenset(
    p.rstrip("/") or "/" for p in (os.path.expanduser("~"), "/tmp", "/private/tmp", "/", "/var/tmp")
)

#: What such sessions are called instead. Named rather than hidden: they are
#: real work, they just did not happen anywhere in particular.
UNPLACED = "(no project)"


def _normalise(path: str) -> str:
    """Trailing slashes off, but root stays root.

    `"/".rstrip("/")` is the empty string, so a naive strip made the filesystem
    root miss its own entry in the set. Both the Python and the SQL side use
    this shape; they must agree or a path is a project in one and not in the
    other.
    """
    return path.rstrip("/") or "/"


def is_placed(project_path: str | None) -> bool:
    """Did this session run somewhere that means something?"""
    if not project_path:
        return False
    return _normalise(project_path) not in _NOT_A_PROJECT_PATHS


#: Order for a session list. "oldest" reads the project as a story from the
#: start; "newest" answers "what was I just doing". Both are wanted often
#: enough that neither can be the only one.
SessionOrder = Literal["newest", "oldest"]


def project_name_sql(alias: str = "c") -> str:
    """Return the canonical SQL expression that names a conversation's project.

    Every project surface must use this expression. Otherwise a session from
    ``/tmp`` appears under ``(no project)`` in one view and disappears from
    another view that compares the generated ``project_name`` column directly.
    ``alias`` is supplied only by this module's query authors, never by a user.
    """
    return f"""
        CASE
          WHEN {alias}.project_path IS NULL
            OR COALESCE(NULLIF(rtrim({alias}.project_path, '/'), ''), '/') = ANY(%(unplaced)s)
          THEN %(unplaced_label)s
          ELSE COALESCE({alias}.project_name, %(unplaced_label)s)
        END
    """


def project_filter_sql(alias: str = "c") -> str:
    """Return the canonical predicate for selecting one project."""
    return f"{project_name_sql(alias)} = %(project)s"


def project_filter_params(project: str) -> dict[str, object]:
    """Parameters shared by every query that uses :func:`project_filter_sql`."""
    return {
        "project": project,
        "unplaced": sorted(_NOT_A_PROJECT_PATHS),
        "unplaced_label": UNPLACED,
    }


def recent(conn, days: int = 7, include_generated: bool = False) -> list[Row]:
    """Projects with activity in the last *days*, busiest first.

    Activity, not creation: a project started months ago and touched yesterday
    is one the reader is currently in, and dating it by its first session would
    hide it. `first_active` is returned as well so the age is still visible.
    """
    gen = "" if include_generated else "AND c.generated_by IS NULL"
    return rows(
        conn,
        f"""
        SELECT CASE
                 WHEN c.project_path IS NULL
                   OR COALESCE(NULLIF(rtrim(c.project_path, '/'), ''), '/') = ANY(%(unplaced)s)
                 THEN %(unplaced_label)s
                 ELSE COALESCE(c.project_name, %(unplaced_label)s)
               END                                  AS project,
               count(*)                             AS sessions,
               COALESCE(sum(c.message_count), 0)    AS messages,
               min(c.started_at)                    AS first_active,
               max(c.started_at)                    AS last_active,
               count(DISTINCT c.source_tool)        AS tools,
               -- Which assistants were used, so a project that spans Claude
               -- Code and Vibe says so. This is the product's whole claim.
               array_remove(array_agg(DISTINCT c.source_tool), NULL) AS tool_names
        FROM conversations c
        WHERE c.started_at >= now() - make_interval(days => %(days)s)
          {gen}
        GROUP BY 1
        ORDER BY sessions DESC, last_active DESC
        """,
        {
            "days": days,
            "unplaced": sorted(_NOT_A_PROJECT_PATHS),
            "unplaced_label": UNPLACED,
        },
    )


def sessions(
    conn,
    project: str,
    *,
    order: SessionOrder = "newest",
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_generated: bool = False,
) -> list[Row]:
    """Sessions in *project*, one row each — never their messages.

    `q` searches within the project: the session's own title and the text of
    its messages. Server-side, because the alternative is shipping 7,284
    messages to the browser to filter them there.
    """
    direction = "ASC" if order == "oldest" else "DESC"
    gen = "" if include_generated else "AND c.generated_by IS NULL"
    search = ""
    params: dict[str, object] = {
        **project_filter_params(project),
        "limit": limit,
        "offset": offset,
    }
    if q:
        params["like"] = f"%{q}%"
        # EXISTS rather than a join: a session matches once, however many of
        # its messages contain the term, and the planner can stop at the first.
        search = """
          AND (c.summary ILIKE %(like)s
               OR EXISTS (SELECT 1 FROM messages m
                          WHERE m.conversation_id = c.id AND m.content ILIKE %(like)s))
        """
    return rows(
        conn,
        f"""
        SELECT c.id,
               c.session_id::text          AS session_id,
               c.summary                   AS title,
               c.message_count,
               c.started_at,
               c.ended_at,
               c.source_tool,
               c.model,
               c.git_branch,
               c.generated_by
        FROM conversations c
        WHERE {project_filter_sql("c")}
          {gen}
          {search}
        ORDER BY c.started_at {direction}, c.id {direction}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )


def session_count(
    conn,
    project: str,
    *,
    q: str | None = None,
    include_generated: bool = False,
) -> int:
    """How many sessions the same filters match, for paging and for honesty.

    Returned alongside a page so the reader is told "23 of 28" rather than
    being left to guess whether the list ended or was cut off.
    """
    gen = "" if include_generated else "AND c.generated_by IS NULL"
    search = ""
    params: dict[str, object] = project_filter_params(project)
    if q:
        params["like"] = f"%{q}%"
        search = """
          AND (c.summary ILIKE %(like)s
               OR EXISTS (SELECT 1 FROM messages m
                          WHERE m.conversation_id = c.id AND m.content ILIKE %(like)s))
        """
    result = rows(
        conn,
        f"""
        SELECT count(*) AS n
        FROM conversations c
        WHERE {project_filter_sql("c")}
          {gen}
          {search}
        """,
        params,
    )
    return int(result[0]["n"]) if result else 0


def hidden_count(conn, project: str) -> int:
    """Machine-generated sessions withheld from this project's list.

    Shown as a number rather than silently dropped: 3,017 of 3,606
    conversations on this corpus were the tool talking to itself, and a view
    that hides them without saying so is a view that lies about how much is
    stored.
    """
    result = rows(
        conn,
        f"""
        SELECT count(*) AS n
        FROM conversations c
        WHERE {project_filter_sql("c")}
          AND c.generated_by IS NOT NULL
        """,
        project_filter_params(project),
    )
    return int(result[0]["n"]) if result else 0
