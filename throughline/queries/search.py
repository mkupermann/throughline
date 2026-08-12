"""Lexical search across every record type.

Extracted verbatim (behaviour-preserving) from ``gui/page_views/search.py``.
Each scope is a standalone function so the HTTP API can fan them out and the
GUI can keep its per-scope expanders, and ``search_all`` runs the set.

The trigram indexes ``idx_memory_content_trgm`` / ``idx_messages_content_trgm``
back the ILIKE predicates on the two large tables.
"""

from __future__ import annotations

import re
from typing import Callable

from ._exec import Row, rows

DEFAULT_SNIPPET = 200


def _like(term: str) -> str:
    return f"%{term}%"


def search_conversations(conn, term: str, limit: int = 20) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, summary, project_name, started_at, message_count
        FROM conversations
        WHERE summary ILIKE %s OR project_name ILIKE %s
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (like, like, limit),
    )


def search_messages(conn, term: str, limit: int = 30, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT m.id,
               m.conversation_id,
               c.summary AS titel,
               m.role::text AS role,
               substring(m.content, 1, %s) AS snippet,
               m.created_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.content ILIKE %s
        ORDER BY m.created_at DESC
        LIMIT %s
        """,
        (snippet, like, limit),
    )


def search_memory(conn, term: str, limit: int = 30, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id,
               category::text AS category,
               substring(content, 1, %s) AS content,
               confidence,
               project_name,
               tags
        FROM memory_chunks
        WHERE content ILIKE %s OR %s = ANY(tags) OR project_name ILIKE %s
        ORDER BY confidence DESC
        LIMIT %s
        """,
        (snippet, like, term, like, limit),
    )


def search_skills(conn, term: str, limit: int = 20, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, name, substring(description, 1, %s) AS description, use_count
        FROM skills
        WHERE name ILIKE %s OR description ILIKE %s
        ORDER BY COALESCE(file_modified, last_used, created_at) DESC NULLS LAST
        LIMIT %s
        """,
        (snippet, like, like, limit),
    )


def search_projects(conn, term: str, limit: int = 20) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, name, description, status::text AS status
        FROM projects
        WHERE name ILIKE %s OR description ILIKE %s
        ORDER BY created_at DESC NULLS LAST
        LIMIT %s
        """,
        (like, like, limit),
    )


def search_prompts(conn, term: str, limit: int = 20, snippet: int = DEFAULT_SNIPPET) -> list[Row]:
    like = _like(term)
    return rows(
        conn,
        """
        SELECT id, name, category, substring(content, 1, %s) AS content, tags
        FROM prompts
        WHERE name ILIKE %s OR content ILIKE %s OR category ILIKE %s
        ORDER BY created_at DESC NULLS LAST
        LIMIT %s
        """,
        (snippet, like, like, like, limit),
    )


#: Scope name -> query function. The GUI, the CLI and the API all iterate this
#: rather than hard-coding the list of searchable record types.
SCOPES: dict[str, Callable[..., list[Row]]] = {
    "conversations": search_conversations,
    "messages": search_messages,
    "memory": search_memory,
    "skills": search_skills,
    "projects": search_projects,
    "prompts": search_prompts,
}

DEFAULT_LIMITS: dict[str, int] = {
    "conversations": 20,
    "messages": 30,
    "memory": 30,
    "skills": 20,
    "projects": 20,
    "prompts": 20,
}


def search_all(
    conn,
    term: str,
    scopes: list[str] | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, list[Row]]:
    """Run every requested scope and return ``{scope: rows}``.

    Unknown scope names raise, rather than being silently dropped — a typo in
    a caller must not look like "no results".
    """
    if not term:
        return {}
    wanted = list(SCOPES) if scopes is None else list(scopes)
    unknown = [s for s in wanted if s not in SCOPES]
    if unknown:
        raise ValueError(f"unknown search scope(s): {unknown}; known: {sorted(SCOPES)}")

    caps = dict(DEFAULT_LIMITS)
    if limits:
        caps.update(limits)

    return {scope: SCOPES[scope](conn, term, limit=caps[scope]) for scope in wanted}


#: Words that carry no retrieval signal. German and English, because questions
#: arrive in both. Kept deliberately short — an aggressive list would strip
#: terms that are ordinary words in one language and identifiers in the other.
_STOPWORDS = frozenset("""
about after again against also because been before being between both could
does doing during each from have here into just more most only other over
same should some such than that their them then there these they this those
through under until very what when where which while with would your
aber alle allem alles also andere auch beim bereits damit dann dass dein
deine denn dessen dieser dieses doch dort durch eine einem einen einer eines
etwas fuer für gegen habe haben hatte hier ihre immer jede jeder jetzt kann
kein keine machen mehr muss nach nicht noch oder ohne schon sein seine sich
sind soll sowie ueber über und unter viel vom von vor waren warum wenn werden
wieder wird wurde zum zur zwischen
""".split())


def salient_terms(text: str, limit: int = 6) -> list[str]:
    """The words in *text* worth searching for literally.

    A question is not a search pattern. ``lexical_for_answer`` used to pass the
    whole question to a single ``ILIKE %…%``, which asks whether some record
    contains that entire sentence verbatim — on a real question that matched
    nothing, every time, so the literal half of a "hybrid" retriever
    contributed exactly zero. Measured: the question "Wie verhindert
    mail-drafter doppelte Entwürfe beim erneuten Lauf?" returned 0 rows, while
    the single token ``mail-drafter`` returned 12.

    Ranking favours what is unlikely to be common prose — a token carrying a
    digit, hyphen, underscore, dot or an internal capital is almost always an
    identifier, a version or a filename, and those are exactly the things a
    vector search is worst at and a person is most likely to remember.
    """
    words = re.findall(r"[\w][\w.\-/]*", text or "", flags=re.UNICODE)
    seen: dict[str, None] = {}
    for w in words:
        cleaned = w.strip(".-/")
        if len(cleaned) < 4 or cleaned.lower() in _STOPWORDS or cleaned.isdigit():
            continue
        seen.setdefault(cleaned, None)

    def distinctiveness(w: str) -> tuple[int, int]:
        return (1 if _looks_technical(w) else 0, len(w))

    return sorted(seen, key=distinctiveness, reverse=True)[:limit]


#: What a distinctive term is worth against an ordinary one when scoring a
#: record. Five, not two, because the failure it corrects is stark: a record
#: containing `mail-drafter` — the one word that decides the question — scored
#: 1 and lost to records matching four of "verhindert / doppelte / mehrfach /
#: ausführt", which decide nothing. One identifier outweighs a sentence of
#: connective tissue, and the margin has to be wide enough to say so.
_DISTINCTIVE_WEIGHT = 5


def _looks_technical(w: str) -> bool:
    """An identifier, version, path or filename rather than ordinary prose."""
    return any(c.isdigit() or c in "-_./" for c in w) or any(c.isupper() for c in w[1:])


def lexical_for_answer(
    conn,
    term: str,
    limit: int = 12,
    project: str | None = None,
) -> list[Row]:
    """Literal matches over memory chunks and messages, shaped like
    ``semantic.semantic_search`` so the two can be fused.

    Exists because embeddings miss exact strings. The complaint is common
    enough to be a genre — "I'd be looking right at the page that contained
    the literal words in my query and embeddings would fail to find it" — and
    it lands hardest on precisely the things worth asking about later: an error
    code, a flag, a table name, a session id. Nearest-neighbour search over a
    768-dimension embedding has no special affinity for a rare token; a
    trigram index does.

    Matches ANY salient term rather than the whole string (see
    ``salient_terms``), and scores by how *distinctive* the matched terms are
    rather than merely how many matched. Counting alone had the effect of
    ranking connective tissue above meaning: a record holding the question's
    one decisive identifier scored 1 and lost to records matching four generic
    verbs.
    """
    terms = salient_terms(term)
    if not terms:
        return []
    # One ILIKE per term, OR'd — each is trigram-indexed
    # (idx_memory_content_trgm / idx_messages_content_trgm).
    params: dict[str, object] = {"limit": limit, "project": project}
    ors = []
    hits = []
    for i, t in enumerate(terms):
        params[f"t{i}"] = f"%{t}%"
        ors.append(f"c.content ILIKE %(t{i})s")
        weight = _DISTINCTIVE_WEIGHT if _looks_technical(t) else 1
        hits.append(f"(CASE WHEN c.content ILIKE %(t{i})s THEN {weight} ELSE 0 END)")
    where_any = " OR ".join(ors)
    score = " + ".join(hits)

    return rows(
        conn,
        f"""
        WITH mc AS (
            SELECT 'memory_chunk'::text AS source_type,
                   c.id                 AS source_id,
                   c.content,
                   c.category::text     AS category,
                   c.project_name,
                   c.confidence::float  AS confidence,
                   NULL::bigint         AS conversation_id,
                   ({score})::int       AS score
            FROM memory_chunks c
            WHERE ({where_any})
              AND COALESCE(c.status, 'active') <> 'forgotten'
              AND (%(project)s::text IS NULL OR c.project_name = %(project)s)
            ORDER BY score DESC, c.created_at DESC
            LIMIT %(limit)s
        ),
        ms AS (
            SELECT 'message'::text AS source_type,
                   c.id            AS source_id,
                   c.content,
                   c.role::text    AS category,
                   conv.project_name,
                   NULL::float     AS confidence,
                   c.conversation_id,
                   ({score})::int  AS score
            FROM messages c
            JOIN conversations conv ON conv.id = c.conversation_id
            WHERE ({where_any})
              AND (%(project)s::text IS NULL OR conv.project_name = %(project)s)
            ORDER BY score DESC, c.created_at DESC
            LIMIT %(limit)s
        )
        SELECT * FROM (SELECT * FROM mc UNION ALL SELECT * FROM ms) u
        ORDER BY score DESC
        LIMIT %(limit)s
        """,
        params,
    )
