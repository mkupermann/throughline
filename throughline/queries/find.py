"""Unified retrieval across every record type — the ``/find`` surface.

What this replaces
------------------
The Streamlit app spread one job over nine pages. ``search.py`` already ran
six independent ILIKE queries and printed six tables; ``semantic.py`` ran a
vector query on a different page with a different result shape. Neither knew
about the other, so a chunk that matched semantically but not lexically was
only findable if you happened to be on the right page.

How fusion works
----------------
Two retrievers, one ranked list:

* **Lexical** — a *substring filter* plus a *prominence ranker*, split by
  field length. This split is measured, not stylistic:

  ============================================  ========  =============
  strategy on 12k messages (avg 673 chars,      time      recall
  max 481k)                                               vs ILIKE truth
  ============================================  ========  =============
  ``similarity(content, term)``                 887 ms    8 / 474
  ``word_similarity(term, content)``            793 ms    480 / 474
  ILIKE filter + score on ``left(content,600)``  56 ms    480 / 474
  ============================================  ========  =============

  ``similarity()`` compares whole strings, so a long document containing a
  short term scores near zero — 1.7% recall, which is a silently broken
  search. ``word_similarity`` is correct but rechecks trigrams over full
  bodies, and one 481k-character message is enough to blow the latency
  budget. So: **ILIKE decides membership** (exact substring, and what a user
  means by "find X"), and **word_similarity over a bounded prefix decides
  rank** (a term in the opening line is more likely the subject than one
  buried at character 40,000).

  Fuzzy trigram matching is kept where it is cheap *and* most valuable — the
  short fields: names, summaries, project names, categories.
* **Semantic** — pgvector nearest neighbours, but *only* over what is actually
  embedded: memory chunks and messages.

They are combined with **Reciprocal Rank Fusion**::

    score(d) = Σ  1 / (RRF_K + rank_r(d))
             r∈retrievers

RRF is used rather than score normalisation because the two retrievers produce
incomparable numbers — a trigram similarity of 0.4 and a cosine distance of
0.4 mean nothing to each other. Ranks are comparable by construction, and RRF
needs no tuning per corpus.

Honest degradation
------------------
Semantic retrieval needs an embedding backend (to embed the query) *and* a
working pgvector. Either can be absent — a fresh install has no backend, and a
Homebrew upgrade can leave the extension registered but unloadable. When that
happens the search still runs lexically and the result reports
``modes=["lexical"]`` so the UI can say *why* results look thin, instead of
silently returning less than the user asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from ._exec import Row, rows, scalar

Kind = Literal["conversation", "message", "memory", "skill", "project", "prompt"]

#: Every record type the unified search covers.
KINDS: tuple[Kind, ...] = (
    "conversation",
    "message",
    "memory",
    "skill",
    "project",
    "prompt",
)

#: Record types that carry embeddings, and can therefore be retrieved
#: semantically. Everything else is lexical-only, by construction.
SEMANTIC_KINDS: frozenset[str] = frozenset({"memory", "message"})

#: RRF damping constant. 60 is the value from the original Cormack et al.
#: paper and is what most implementations use; it flattens the contribution
#: of the very top ranks enough that one retriever cannot dominate.
RRF_K = 60

#: How deep each retriever goes before fusion. Fusing only the top-N of each
#: is the point of RRF — going deeper adds cost without changing the head of
#: the list, which is all anyone reads.
RETRIEVER_DEPTH = 100

#: Per-kind ceiling in browse mode. Bounds the merge when someone browses
#: with no date range at all across 12k messages.
MAX_BROWSE_PER_KIND = 2000

#: How much of a long text field is scanned for ranking. Membership is
#: already decided by ILIKE, so this only orders results — and bounding it is
#: what keeps one enormous message from dominating the query budget.
RANK_PREFIX = 600


@dataclass
class FindFilters:
    """Facets. Every field is optional; None/empty means 'no constraint'."""

    kinds: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    min_confidence: float | None = None
    since: Any = None
    until: Any = None
    has_embedding: bool | None = None
    #: Provider names (``conversations.source_tool``). App-scope, unlike the
    #: other facets — see design spec §4.2.
    providers: list[str] = field(default_factory=list)

    def wants(self, kind: str) -> bool:
        return not self.kinds or kind in self.kinds


@dataclass
class FindResult:
    items: list[Row]
    total: int
    modes: list[str]
    notes: list[str] = field(default_factory=list)


# ── Lexical retrieval, one query per record type ────────────────────────────
# Each returns a uniform shape so fusion never has to special-case a kind:
#   kind, id, title, snippet, project, occurred_at, category, status,
#   confidence, score, conversation_id


def _lex_memory(conn, term: str, f: FindFilters, limit: int) -> list[Row]:
    clauses, params = _common_memory_filters(f)
    return rows(
        conn,
        f"""
        SELECT 'memory'::text                       AS kind,
               mc.id                                AS id,
               NULL::text                           AS title,
               left(mc.content, 400)                AS snippet,
               mc.project_name                      AS project,
               mc.created_at                        AS occurred_at,
               mc.category::text                    AS category,
               COALESCE(mc.status, 'active')        AS status,
               mc.confidence::float                 AS confidence,
               NULL::bigint                         AS conversation_id,
               GREATEST(
                   word_similarity(%(term)s, left(mc.content, %(prefix)s)),
                   CASE WHEN %(term)s = ANY(mc.tags)              THEN 0.90 ELSE 0 END,
                   CASE WHEN mc.project_name ILIKE %(like)s       THEN 0.55 ELSE 0 END,
                   word_similarity(%(term)s, COALESCE(mc.project_name, '')),
                   0.05
               )                                    AS score
        FROM memory_chunks mc
        WHERE (mc.content ILIKE %(like)s
               OR mc.project_name ILIKE %(like)s
               OR %(term)s <%% COALESCE(mc.project_name, '')
               OR %(term)s = ANY(mc.tags))
          {clauses}
        ORDER BY score DESC, mc.created_at DESC
        LIMIT %(limit)s
        """,
        {"term": term, "like": f"%{term}%", "limit": limit, "prefix": RANK_PREFIX, **params},
    )


def _lex_message(conn, term: str, f: FindFilters, limit: int) -> list[Row]:
    clauses, params = _date_filters(f, "m.created_at")
    clauses += _provider_clause("c", f, params)
    proj = ""
    if f.projects:
        proj = "AND c.project_name = ANY(%(projects)s)"
        params["projects"] = f.projects
    return rows(
        conn,
        f"""
        SELECT 'message'::text            AS kind,
               m.id                       AS id,
               c.summary                  AS title,
               left(m.content, 400)       AS snippet,
               c.project_name             AS project,
               m.created_at               AS occurred_at,
               m.role::text               AS category,
               NULL::text                 AS status,
               NULL::float                AS confidence,
               m.conversation_id          AS conversation_id,
               GREATEST(
                   word_similarity(%(term)s, left(m.content, %(prefix)s)),
                   0.05
               )                          AS score
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        -- Messages from sessions a person had. The join is already here for
        -- the project filter, so this costs nothing and keeps the tool's own
        -- prompts out of message results.
        WHERE c.generated_by IS NULL
          AND m.content ILIKE %(like)s
          {clauses}
          {proj}
        ORDER BY score DESC, m.created_at DESC
        LIMIT %(limit)s
        """,
        {"term": term, "like": f"%{term}%", "limit": limit, "prefix": RANK_PREFIX, **params},
    )


def _lex_conversation(conn, term: str, f: FindFilters, limit: int) -> list[Row]:
    clauses, params = _date_filters(f, "c.started_at")
    clauses += _provider_clause("c", f, params)
    proj = ""
    if f.projects:
        proj = "AND c.project_name = ANY(%(projects)s)"
        params["projects"] = f.projects
    return rows(
        conn,
        f"""
        SELECT 'conversation'::text  AS kind,
               c.id                  AS id,
               c.summary             AS title,
               left(COALESCE(c.summary, ''), 400) AS snippet,
               c.project_name        AS project,
               c.started_at          AS occurred_at,
               c.model               AS category,
               NULL::text            AS status,
               NULL::float           AS confidence,
               c.id                  AS conversation_id,
               GREATEST(
                   word_similarity(%(term)s, COALESCE(c.summary, '')),
                   word_similarity(%(term)s, COALESCE(c.project_name, '')),
                   CASE WHEN c.project_name ILIKE %(like)s THEN 0.55 ELSE 0 END,
                   0.05
               )                     AS score
        FROM conversations c
        -- Sessions a person had. The tool's own `claude -p` calls
        -- outnumbered real ones ten to one, so every result page was
        -- mostly Throughline quoting itself back at the reader.
        WHERE c.generated_by IS NULL
          AND (COALESCE(c.summary, '') ILIKE %(like)s
               OR c.project_name ILIKE %(like)s
               OR %(term)s <%% COALESCE(c.summary, '')
               OR %(term)s <%% COALESCE(c.project_name, ''))
          {clauses}
          {proj}
        ORDER BY score DESC, c.started_at DESC
        LIMIT %(limit)s
        """,
        {"term": term, "like": f"%{term}%", "limit": limit, **params},
    )


def _lex_skill(conn, term: str, f: FindFilters, limit: int) -> list[Row]:
    if f.providers:
        return []
    return rows(
        conn,
        """
        SELECT 'skill'::text                       AS kind,
               s.id                                AS id,
               s.name                              AS title,
               left(COALESCE(s.description, ''), 400) AS snippet,
               NULL::text                          AS project,
               COALESCE(s.file_modified, s.last_used, s.created_at) AS occurred_at,
               NULL::text                          AS category,
               NULL::text                          AS status,
               NULL::float                         AS confidence,
               NULL::bigint                        AS conversation_id,
               GREATEST(
                   word_similarity(%(term)s, s.name),
                   word_similarity(%(term)s, left(COALESCE(s.description, ''), %(prefix)s)),
                   0.05
               )                                   AS score
        FROM skills s
        WHERE s.name ILIKE %(like)s
           OR COALESCE(s.description, '') ILIKE %(like)s
           OR %(term)s <%% s.name
        ORDER BY score DESC
        LIMIT %(limit)s
        """,
        {"term": term, "like": f"%{term}%", "limit": limit, "prefix": RANK_PREFIX},
    )


def _lex_project(conn, term: str, f: FindFilters, limit: int) -> list[Row]:
    """Projects, sourced from *observed* names rather than the registry.

    `projects` is enrichment: on a typical install it lags reality badly (53
    registered rows against 81 names actually in use). Searching only the
    registry made most of a user's projects unfindable even though their
    memory was right there. `id` is the registry id where one exists, and
    NULL otherwise — the UI routes on name.
    """
    if f.providers:
        return []
    return rows(
        conn,
        """
        WITH observed AS (
            SELECT name, sum(chunks)::bigint AS chunks, max(last_activity) AS last_activity
            FROM (
                SELECT project_name AS name, count(*) AS chunks, max(created_at) AS last_activity
                FROM memory_chunks WHERE project_name IS NOT NULL GROUP BY project_name
                UNION ALL
                SELECT project_name, count(*), max(started_at)
                FROM conversations WHERE project_name IS NOT NULL GROUP BY project_name
            ) u GROUP BY name
        )
        SELECT 'project'::text AS kind,
               COALESCE(p.id, 0)                     AS id,
               o.name                                AS title,
               left(COALESCE(p.description, ''), 400) AS snippet,
               o.name                                AS project,
               o.last_activity                       AS occurred_at,
               NULL::text                            AS category,
               p.status::text                        AS status,
               NULL::float                           AS confidence,
               NULL::bigint                          AS conversation_id,
               GREATEST(
                   word_similarity(%(term)s, o.name),
                   word_similarity(%(term)s, COALESCE(p.description, '')),
                   0.05
               )                                     AS score
        FROM observed o
        LEFT JOIN projects p ON p.name = o.name
        WHERE o.name ILIKE %(like)s
           OR COALESCE(p.description, '') ILIKE %(like)s
           OR %(term)s <%% o.name
        ORDER BY score DESC, o.last_activity DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"term": term, "like": f"%{term}%", "limit": limit},
    )


def _lex_prompt(conn, term: str, f: FindFilters, limit: int) -> list[Row]:
    if f.providers:
        return []
    return rows(
        conn,
        """
        SELECT 'prompt'::text   AS kind,
               p.id             AS id,
               p.name           AS title,
               left(p.content, 400) AS snippet,
               NULL::text       AS project,
               p.created_at     AS occurred_at,
               p.category       AS category,
               NULL::text       AS status,
               NULL::float      AS confidence,
               NULL::bigint     AS conversation_id,
               GREATEST(
                   word_similarity(%(term)s, p.name),
                   word_similarity(%(term)s, left(p.content, %(prefix)s)),
                   CASE WHEN p.category ILIKE %(like)s THEN 0.55 ELSE 0 END,
                   0.05
               )                AS score
        FROM prompts p
        WHERE p.name ILIKE %(like)s
           OR p.content ILIKE %(like)s
           OR p.category ILIKE %(like)s
           OR %(term)s <%% p.name
        ORDER BY score DESC
        LIMIT %(limit)s
        """,
        {"term": term, "like": f"%{term}%", "limit": limit, "prefix": RANK_PREFIX},
    )


def _browse_memory(conn, f: FindFilters, limit: int) -> list[Row]:
    clauses, params = _common_memory_filters(f)
    return rows(
        conn,
        f"""
        SELECT 'memory'::text AS kind, mc.id, NULL::text AS title,
               left(mc.content, 400) AS snippet, mc.project_name AS project,
               mc.created_at AS occurred_at, mc.category::text AS category,
               COALESCE(mc.status,'active') AS status, mc.confidence::float AS confidence,
               NULL::bigint AS conversation_id, 0.0::float AS score
        FROM memory_chunks mc
        WHERE TRUE {clauses}
        ORDER BY mc.created_at DESC, mc.id DESC
        LIMIT %(limit)s
        """,
        {"limit": limit, **params},
    )


def _browse_message(conn, f: FindFilters, limit: int) -> list[Row]:
    clauses, params = _date_filters(f, "m.created_at")
    clauses += _provider_clause("c", f, params)
    proj = ""
    if f.projects:
        proj = "AND c.project_name = ANY(%(projects)s)"
        params["projects"] = f.projects
    return rows(
        conn,
        f"""
        SELECT 'message'::text AS kind, m.id, c.summary AS title,
               left(m.content, 400) AS snippet, c.project_name AS project,
               m.created_at AS occurred_at, m.role::text AS category,
               NULL::text AS status, NULL::float AS confidence,
               m.conversation_id, 0.0::float AS score
        FROM messages m JOIN conversations c ON c.id = m.conversation_id
        -- Human sessions only, as in the search variant above.
        WHERE c.generated_by IS NULL {clauses} {proj}
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT %(limit)s
        """,
        {"limit": limit, **params},
    )


def _browse_conversation(conn, f: FindFilters, limit: int) -> list[Row]:
    clauses, params = _date_filters(f, "c.started_at")
    clauses += _provider_clause("c", f, params)
    proj = ""
    if f.projects:
        proj = "AND c.project_name = ANY(%(projects)s)"
        params["projects"] = f.projects
    return rows(
        conn,
        f"""
        SELECT 'conversation'::text AS kind, c.id, c.summary AS title,
               left(COALESCE(c.summary,''), 400) AS snippet, c.project_name AS project,
               c.started_at AS occurred_at, c.model AS category,
               NULL::text AS status, NULL::float AS confidence,
               c.id AS conversation_id, 0.0::float AS score
        FROM conversations c
        -- Sessions a person had. The tool's own `claude -p` calls
        -- outnumbered real ones ten to one, so every result page was
        -- mostly Throughline quoting itself back at the reader.
        WHERE c.generated_by IS NULL AND c.started_at IS NOT NULL {clauses} {proj}
        ORDER BY c.started_at DESC, c.id DESC
        LIMIT %(limit)s
        """,
        {"limit": limit, **params},
    )


def _browse_skill(conn, f: FindFilters, limit: int) -> list[Row]:
    if f.providers:
        return []
    return rows(
        conn,
        """
        SELECT 'skill'::text AS kind, s.id, s.name AS title,
               left(COALESCE(s.description,''), 400) AS snippet, NULL::text AS project,
               COALESCE(s.file_modified, s.last_used, s.created_at) AS occurred_at,
               NULL::text AS category, NULL::text AS status, NULL::float AS confidence,
               NULL::bigint AS conversation_id, 0.0::float AS score
        FROM skills s
        ORDER BY occurred_at DESC NULLS LAST, s.id DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )


def _browse_project(conn, f: FindFilters, limit: int) -> list[Row]:
    if f.providers:
        return []
    return rows(
        conn,
        """
        WITH observed AS (
            SELECT name, max(last_activity) AS last_activity
            FROM (
                SELECT project_name AS name, max(created_at) AS last_activity
                FROM memory_chunks WHERE project_name IS NOT NULL GROUP BY project_name
                UNION ALL
                SELECT project_name, max(started_at)
                FROM conversations WHERE project_name IS NOT NULL GROUP BY project_name
            ) u GROUP BY name
        )
        SELECT 'project'::text AS kind, COALESCE(p.id, 0) AS id, o.name AS title,
               left(COALESCE(p.description,''), 400) AS snippet, o.name AS project,
               o.last_activity AS occurred_at, NULL::text AS category,
               p.status::text AS status, NULL::float AS confidence,
               NULL::bigint AS conversation_id, 0.0::float AS score
        FROM observed o
        LEFT JOIN projects p ON p.name = o.name
        ORDER BY o.last_activity DESC NULLS LAST, o.name DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )


def _browse_prompt(conn, f: FindFilters, limit: int) -> list[Row]:
    if f.providers:
        return []
    return rows(
        conn,
        """
        SELECT 'prompt'::text AS kind, p.id, p.name AS title,
               left(p.content, 400) AS snippet, NULL::text AS project,
               p.created_at AS occurred_at, p.category, NULL::text AS status,
               NULL::float AS confidence, NULL::bigint AS conversation_id,
               0.0::float AS score
        FROM prompts p
        ORDER BY p.created_at DESC NULLS LAST, p.id DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
    )


_BROWSE = {
    "memory": _browse_memory,
    "message": _browse_message,
    "conversation": _browse_conversation,
    "skill": _browse_skill,
    "project": _browse_project,
    "prompt": _browse_prompt,
}

_LEXICAL = {
    "memory": _lex_memory,
    "message": _lex_message,
    "conversation": _lex_conversation,
    "skill": _lex_skill,
    "project": _lex_project,
    "prompt": _lex_prompt,
}


# ── Filter fragment builders ────────────────────────────────────────────────

def _provider_clause(alias: str, filters: FindFilters, params: dict) -> str:
    """SQL fragment restricting *alias* (a conversations alias) by provider."""
    if not filters.providers:
        return ""
    params["providers"] = list(filters.providers)
    return f" AND {alias}.source_tool = ANY(%(providers)s)"


def _provider_clause_via_conversation(source_col: str, filters: FindFilters, params: dict) -> str:
    """For tables that reach conversations by id, e.g. memory_chunks.source_id.

    ``source_id`` is polymorphic — no FK, no CHECK constraint — and means "a
    conversation id" only when the row's ``source_type = 'conversation'``.
    Without that guard, a row of some other source_type whose source_id
    happens to collide with a real conversation id would be silently (and
    wrongly) attributed to that conversation's provider. Mirrors the same
    guard already used in ``queries/activity.py``.
    """
    if not filters.providers:
        return ""
    params["providers"] = list(filters.providers)
    alias, _, _ = source_col.rpartition(".")
    return (
        f" AND {alias}.source_type = 'conversation'"
        f" AND EXISTS (SELECT 1 FROM conversations pc "
        f"WHERE pc.id = {source_col} AND pc.source_tool = ANY(%(providers)s))"
    )


def _date_filters(f: FindFilters, column: str) -> tuple[str, dict[str, Any]]:
    clauses, params = "", {}
    if f.since is not None:
        clauses += f" AND {column} >= %(since)s"
        params["since"] = f.since
    if f.until is not None:
        clauses += f" AND {column} <= %(until)s"
        params["until"] = f.until
    return clauses, params


def _common_memory_filters(f: FindFilters) -> tuple[str, dict[str, Any]]:
    clauses, params = _date_filters(f, "mc.created_at")
    # Soft-deleted chunks must not surface in search — otherwise "forget"
    # only hides a row from one list and the memory is still findable
    # everywhere else, which is worse than not offering the action at all.
    # An explicit status filter can still ask for them by name.
    if not f.statuses:
        from .curate import HIDDEN_STATUSES

        clauses += " AND COALESCE(mc.status,'active') <> ALL(%(hidden)s)"
        params["hidden"] = list(HIDDEN_STATUSES)
    if f.categories:
        clauses += " AND mc.category::text = ANY(%(categories)s)"
        params["categories"] = f.categories
    if f.projects:
        clauses += " AND mc.project_name = ANY(%(projects)s)"
        params["projects"] = f.projects
    if f.statuses:
        clauses += " AND COALESCE(mc.status, 'active') = ANY(%(statuses)s)"
        params["statuses"] = f.statuses
    if f.tags:
        clauses += " AND mc.tags && %(tags)s"
        params["tags"] = f.tags
    if f.min_confidence is not None:
        clauses += " AND mc.confidence >= %(min_conf)s"
        params["min_conf"] = f.min_confidence
    if f.has_embedding is not None:
        op = "EXISTS" if f.has_embedding else "NOT EXISTS"
        clauses += (
            f" AND {op} (SELECT 1 FROM embeddings e "
            "WHERE e.source_type = 'memory_chunk' AND e.source_id = mc.id)"
        )
    clauses += _provider_clause_via_conversation("mc.source_id", f, params)
    return clauses, params


# ── Semantic retrieval ──────────────────────────────────────────────────────

def _semantic(
    conn,
    vector_literal: str,
    model: str,
    column: str,
    f: FindFilters,
    limit: int,
) -> list[Row]:
    """Nearest neighbours over the embedded record types.

    Shape matches the lexical retrievers exactly, so fusion is kind-agnostic.
    Filters are applied *after* the vector join for the same reason documented
    in ``queries/semantic.py``: bounding the candidate set before a selective
    filter silently under-returns.
    """
    from ._exec import check_embedding_column

    col = check_embedding_column(column)
    mem_ok = f.wants("memory")
    msg_ok = f.wants("message")
    if not (mem_ok or msg_ok):
        return []

    mem_clauses, mem_params = _common_memory_filters(f)
    msg_clauses, msg_params = _date_filters(f, "m.created_at")
    msg_clauses += _provider_clause("c", f, msg_params)
    msg_proj = ""
    if f.projects:
        msg_proj = "AND c.project_name = ANY(%(msg_projects)s)"
        msg_params["msg_projects"] = f.projects

    parts = []
    if mem_ok:
        parts.append(
            f"""
            SELECT 'memory'::text AS kind, mc.id AS id, NULL::text AS title,
                   left(mc.content, 400) AS snippet, mc.project_name AS project,
                   mc.created_at AS occurred_at, mc.category::text AS category,
                   COALESCE(mc.status, 'active') AS status,
                   mc.confidence::float AS confidence,
                   NULL::bigint AS conversation_id,
                   1 - (e.{col} <=> %(vec)s::vector) AS score
            FROM embeddings e
            JOIN memory_chunks mc ON mc.id = e.source_id
            WHERE e.source_type = 'memory_chunk' AND e.model = %(model)s
              AND e.{col} IS NOT NULL
              {mem_clauses}
            """
        )
    if msg_ok:
        parts.append(
            f"""
            SELECT 'message'::text AS kind, m.id AS id, c.summary AS title,
                   left(m.content, 400) AS snippet, c.project_name AS project,
                   m.created_at AS occurred_at, m.role::text AS category,
                   NULL::text AS status, NULL::float AS confidence,
                   m.conversation_id AS conversation_id,
                   1 - (e.{col} <=> %(vec)s::vector) AS score
            FROM embeddings e
            JOIN messages m      ON m.id = e.source_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE e.source_type = 'message' AND e.model = %(model)s
              AND e.{col} IS NOT NULL
              {msg_clauses}
              {msg_proj}
            """
        )

    sql = " UNION ALL ".join(parts)
    return rows(
        conn,
        f"SELECT * FROM ({sql}) s ORDER BY score DESC LIMIT %(limit)s",
        {
            "vec": vector_literal,
            "model": model,
            "limit": limit,
            **mem_params,
            **msg_params,
        },
    )


# ── Fusion ──────────────────────────────────────────────────────────────────

def _rrf(ranked_lists: Iterable[Sequence[Row]], k: int = RRF_K) -> list[Row]:
    """Reciprocal Rank Fusion over several ranked lists.

    Ties are broken by the number of retrievers that found the document, then
    by recency — a result both retrievers agree on outranks one only a single
    retriever saw at the same fused score.
    """
    fused: dict[tuple[str, int], dict[str, Any]] = {}
    for ranked in ranked_lists:
        for rank, row in enumerate(ranked, start=1):
            key = (str(row["kind"]), int(row["id"]))
            entry = fused.get(key)
            if entry is None:
                entry = dict(row)
                entry["_rrf"] = 0.0
                entry["_hits"] = 0
                fused[key] = entry
            entry["_rrf"] += 1.0 / (k + rank)
            entry["_hits"] += 1

    out = sorted(
        fused.values(),
        key=lambda r: (
            -r["_rrf"],
            -r["_hits"],
            -(r["occurred_at"].timestamp() if r.get("occurred_at") else 0),
        ),
    )
    for r in out:
        r["score"] = round(r.pop("_rrf"), 6)
        r["retrievers"] = r.pop("_hits")
    return out


def _browse_sort_key(r: Row) -> tuple:
    occurred = r.get("occurred_at")
    return (
        occurred is not None,
        occurred.timestamp() if occurred is not None else 0.0,
        str(r.get("kind")),
        int(r.get("id") or 0),
    )


def browse(
    conn,
    filters: "FindFilters | None" = None,
    limit: int = 200,
    offset: int = 0,
) -> "FindResult":
    """Filtered listing with no search text, ordered by time.

    Find has to answer "what happened in June?" as well as "where did I say
    X?". Search alone cannot: with no query there is nothing to rank, so the
    surface would be empty exactly when the user is browsing. The Timeline and
    Graph views depend on this — and it is what replaces the old Calendar page,
    which was browse-only.
    """
    f = filters or FindFilters()
    # Each retriever must supply enough rows to fill the requested page after
    # the merge, or paging past the first screen would silently lose records
    # from whichever kind happened to sort late.
    per_kind = min(offset + limit, MAX_BROWSE_PER_KIND)

    ranked: list[Row] = []
    capped: list[str] = []
    for kind, fn in _BROWSE.items():
        if not f.wants(kind):
            continue
        got = fn(conn, f, per_kind)
        if len(got) >= per_kind:
            capped.append(kind)
        ranked.extend(got)

    # A total order, not just a date order. Many chunks share a timestamp to
    # the second, and sorting on date alone left ties to fall in whatever
    # order the candidate lists happened to arrive — which differs per page
    # size, so page 2 could repeat rows from page 1. (kind, id) breaks ties
    # deterministically.
    ranked.sort(key=_browse_sort_key, reverse=True)
    for r in ranked:
        r["score"] = 0.0
        r["retrievers"] = 1

    notes: list[str] = []
    if capped:
        # `total` counts what was fetched, not what exists. Saying so beats a
        # number that looks authoritative and is not.
        notes.append(
            f"Showing the most recent {per_kind:,} per type ({', '.join(sorted(capped))}); "
            "narrow the date range or filters to see further back."
        )

    return FindResult(
        items=ranked[offset : offset + limit],
        total=len(ranked),
        modes=["browse"],
        notes=notes,
    )


def find(
    conn,
    query: str,
    filters: FindFilters | None = None,
    limit: int = 30,
    offset: int = 0,
    depth: int = RETRIEVER_DEPTH,
    vector_literal: str | None = None,
    model: str | None = None,
    column: str | None = None,
) -> FindResult:
    """Run the hybrid search.

    Pass ``vector_literal``/``model``/``column`` to enable the semantic leg;
    omit them and the search is lexical-only and says so in ``modes``.
    """
    f = filters or FindFilters()
    query = (query or "").strip()
    if not query:
        return FindResult(items=[], total=0, modes=[], notes=["Empty query."])

    modes: list[str] = []
    notes: list[str] = []
    ranked: list[list[Row]] = []

    for kind, fn in _LEXICAL.items():
        if f.wants(kind):
            # No score floor: membership was decided by the WHERE clause.
            # Filtering on score here would drop genuine matches whose only
            # occurrence sits past RANK_PREFIX — the very bug this replaced.
            hits = fn(conn, query, f, depth)
            if hits:
                ranked.append(hits)
    if ranked:
        modes.append("lexical")

    if vector_literal and model and column:
        try:
            sem = _semantic(conn, vector_literal, model, column, f, depth)
            if sem:
                ranked.append(sem)
            modes.append("semantic")
        except Exception as exc:  # pgvector missing/broken — degrade, don't fail
            try:
                conn.rollback()
            except Exception:
                pass
            notes.append(f"Semantic search unavailable: {exc}")
    else:
        notes.append(
            "Semantic search is off — no embedding backend configured, so only "
            "literal text matches are shown."
        )

    fused = _rrf(ranked)
    return FindResult(
        items=fused[offset : offset + limit],
        total=len(fused),
        modes=modes,
        notes=notes,
    )


# ── Facets ──────────────────────────────────────────────────────────────────

def facets(conn) -> dict[str, list[dict[str, Any]]]:
    """Available facet values with counts, for the filter rail.

    Projects come from ``observed_project_names`` rather than the ``projects``
    table, which is empty on some installs while dozens of distinct
    ``project_name`` values exist in the data.
    """
    cats = rows(
        conn,
        "SELECT category::text AS value, count(*) AS n FROM memory_chunks "
        "GROUP BY category ORDER BY n DESC",
    )
    statuses = rows(
        conn,
        "SELECT COALESCE(status, 'active') AS value, count(*) AS n FROM memory_chunks "
        "GROUP BY COALESCE(status, 'active') ORDER BY n DESC",
    )
    projects = rows(
        conn,
        """
        SELECT name AS value, sum(n)::bigint AS n FROM (
            SELECT project_name AS name, count(*) AS n FROM memory_chunks
            WHERE project_name IS NOT NULL GROUP BY project_name
            UNION ALL
            SELECT project_name, count(*) FROM conversations
            -- The project facet offers a filter; it must count what that
            -- filter can return.
            WHERE project_name IS NOT NULL AND generated_by IS NULL
            GROUP BY project_name
        ) u GROUP BY name ORDER BY n DESC LIMIT 100
        """,
    )
    tags = rows(
        conn,
        "SELECT t AS value, count(*) AS n FROM memory_chunks, unnest(tags) t "
        "GROUP BY t ORDER BY n DESC LIMIT 50",
    )
    kinds = [
        {"value": "memory", "n": int(scalar(conn, "SELECT count(*) FROM memory_chunks", (), 0) or 0)},
        {
            "value": "message",
            # Same rule as conversations below: the message searches join
            # `conversations` and filter on it, so an unfiltered count here
            # advertised 85,407 where 73,818 can be reached.
            "n": int(
                scalar(
                    conn,
                    "SELECT count(*) FROM messages m "
                    "JOIN conversations c ON c.id = m.conversation_id "
                    "WHERE c.generated_by IS NULL",
                    (),
                    0,
                )
                or 0
            ),
        },
        {
            "value": "conversation",
            # Must match what the searches actually return. This counted every
            # stored row — 3,606 against 330 reachable — so the facet rail
            # promised results no query could produce.
            "n": int(
                scalar(
                    conn,
                    "SELECT count(*) FROM conversations WHERE generated_by IS NULL",
                    (),
                    0,
                )
                or 0
            ),
        },
        {"value": "skill", "n": int(scalar(conn, "SELECT count(*) FROM skills", (), 0) or 0)},
        {"value": "project", "n": int(scalar(conn, "SELECT count(*) FROM projects", (), 0) or 0)},
        {"value": "prompt", "n": int(scalar(conn, "SELECT count(*) FROM prompts", (), 0) or 0)},
    ]
    return {
        "kinds": kinds,
        "categories": [dict(r) for r in cats],
        "statuses": [dict(r) for r in statuses],
        "projects": [dict(r) for r in projects],
        "tags": [dict(r) for r in tags],
    }
