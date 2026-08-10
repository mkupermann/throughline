"""Low-level execution helpers shared by every query module.

Deliberately free of pandas and Streamlit: this package is imported by the
CLI, the MCP server and (from Phase 1) the HTTP API, none of which should
pull a dataframe library into their import graph. Callers that want a
``DataFrame`` wrap the returned ``list[dict]`` themselves.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import psycopg2
import psycopg2.extras

Params = Sequence[Any] | Mapping[str, Any] | None
Row = dict[str, Any]


def rows(conn, sql: str, params: Params = None) -> list[Row]:
    """Execute *sql* and return every row as a plain dict."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        return [dict(r) for r in cur.fetchall()]


def one(conn, sql: str, params: Params = None) -> Row | None:
    """Execute *sql* and return the first row, or ``None``."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return None
        row = cur.fetchone()
        return dict(row) if row is not None else None


def scalar(conn, sql: str, params: Params = None, default: Any = None) -> Any:
    """Execute *sql* and return the first column of the first row."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return default
        return row[0]


def execute(conn, sql: str, params: Params = None) -> int:
    """Execute a statement and return the affected row count.

    Does not commit — transaction control belongs to the caller so that a
    multi-statement mutation stays atomic.
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def execute_batch(conn, sql: str, argslist: Iterable[Sequence[Any]], page_size: int = 500) -> int:
    """Multi-row INSERT/UPDATE via ``execute_values``.

    *sql* must contain a single ``%s`` placeholder standing in for the VALUES
    list, e.g. ``INSERT INTO t (a, b) VALUES %s``. Returns the row count.
    """
    values = list(argslist)
    if not values:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=page_size)
        return cur.rowcount


# ── Identifier safety ────────────────────────────────────────────────────────
# A handful of queries need to interpolate a column name (the embedding column
# depends on the active backend's dimensionality). Never build those from raw
# caller input — whitelist instead.

EMBEDDING_COLUMNS = frozenset({"embedding_1536", "embedding_768"})


def check_embedding_column(col: str) -> str:
    """Return *col* if it is a known embedding column, else raise."""
    if col not in EMBEDDING_COLUMNS:
        raise ValueError(
            f"unknown embedding column {col!r}; expected one of {sorted(EMBEDDING_COLUMNS)}"
        )
    return col


def _sort_clause(sort: str, allowed: Mapping[str, str], default: str) -> str:
    """Map a caller-supplied sort key onto a whitelisted ORDER BY fragment."""
    return allowed.get(sort, allowed[default])
