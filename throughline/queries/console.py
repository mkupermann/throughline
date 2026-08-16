"""Read-only SQL execution for the ``/console`` surface.

How writes are actually prevented
---------------------------------
Every statement runs inside ``BEGIN READ ONLY``. The rejection then comes from
PostgreSQL, not from this process:

    ERROR:  cannot execute INSERT in a read-only transaction

Verified against INSERT, UPDATE, DELETE, CREATE, DROP and TRUNCATE. That
matters because the obvious alternative — pattern-matching the SQL text for
dangerous keywords — is security theatre: it is defeated by comments, casing,
CTEs (``WITH x AS (DELETE … RETURNING *) SELECT * FROM x``), and multi-statement
strings, and it blocks legitimate queries that merely mention the word
"delete". A guard the database enforces cannot be talked around by clever SQL.

An additional dedicated read-only *role* can be supplied via
``THROUGHLINE_CONSOLE_DSN`` for defence in depth (see ``api/deps.py``); the
read-only transaction is applied either way.

What this does NOT protect against, stated plainly
--------------------------------------------------
A read-only transaction blocks writes to the current database. It does not
stop a volatile function with side effects elsewhere (``dblink``,
``pg_advisory_lock``, superuser file functions), and it does not stop a query
from being expensive — that is what the statement timeout and row cap are for.
For a single-user local tool whose operator already has full ``psql`` access,
the console's job is preventing accidents, not defending against its own user.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import psycopg2
import psycopg2.extras

#: Statements are cancelled by Postgres after this. A console query that runs
#: longer than a few seconds is a mistake, and holding a pooled connection
#: open indefinitely starves the rest of the app.
DEFAULT_TIMEOUT_MS = 15_000

#: Hard cap on returned rows. `SELECT * FROM messages` is a keystroke away and
#: would otherwise try to serialise 12k rows of message bodies into JSON.
DEFAULT_MAX_ROWS = 1_000


@dataclass
class ConsoleResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float = 0.0
    notices: list[str] = field(default_factory=list)
    error: str | None = None
    error_hint: str | None = None


def _jsonable(value: Any) -> Any:
    """Render a value for JSON without losing what it was."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, memoryview):
        return f"<{len(value)} bytes>"
    return str(value)


def run_query(
    conn,
    sql: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> ConsoleResult:
    """Execute *sql* read-only and return a renderable result.

    Errors are returned rather than raised: a syntax error is a normal outcome
    of using a SQL console, not an exception the HTTP layer should turn into a
    500.
    """
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return ConsoleResult(error="Enter a query.")

    started = time.perf_counter()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # READ ONLY must be set before the transaction does any work, so
            # it is the first statement after the implicit BEGIN.
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
            cur.execute(sql)

            result = ConsoleResult(duration_ms=(time.perf_counter() - started) * 1000)

            if cur.description is None:
                # DO, SET, EXPLAIN-less utility statements: no result set.
                result.row_count = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                result.notices = [n.strip() for n in getattr(conn, "notices", [])][-10:]
                return result

            result.columns = [d.name for d in cur.description]
            fetched = cur.fetchmany(max_rows + 1)
            result.truncated = len(fetched) > max_rows
            rows = fetched[:max_rows]
            result.rows = [[_jsonable(r[c]) for c in result.columns] for r in rows]
            result.row_count = len(result.rows)
            result.duration_ms = (time.perf_counter() - started) * 1000
            result.notices = [n.strip() for n in getattr(conn, "notices", [])][-10:]
            return result

    except psycopg2.errors.QueryCanceled:
        return ConsoleResult(
            error=f"Query cancelled after {timeout_ms} ms.",
            error_hint="Add a LIMIT, or narrow the WHERE clause.",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    except psycopg2.Error as exc:
        pgerror = (exc.pgerror or str(exc)).strip()
        hint = None
        if "read-only transaction" in pgerror:
            hint = (
                "The console is read-only and PostgreSQL rejected this statement. "
                "Use the Curate surface for changes, or the CLI for maintenance."
            )
        return ConsoleResult(
            error=pgerror,
            error_hint=hint,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        # The transaction is never committed — nothing to commit, and rolling
        # back releases the snapshot promptly.
        try:
            conn.rollback()
        except Exception:
            pass


def schema(conn) -> dict[str, Any]:
    """Tables, columns and enum values, for editor completion."""
    from ._exec import rows as _rows

    cols = _rows(
        conn,
        """
        SELECT c.table_name, c.column_name, c.data_type, c.ordinal_position
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_name = c.table_name AND t.table_schema = c.table_schema
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position
        """,
    )
    tables: dict[str, list[dict[str, str]]] = {}
    for r in cols:
        tables.setdefault(r["table_name"], []).append({"name": r["column_name"], "type": r["data_type"]})

    enums = _rows(
        conn,
        """
        SELECT t.typname AS name, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) AS values
        FROM pg_type t
        JOIN pg_enum e ON e.enumtypid = t.oid
        GROUP BY t.typname
        ORDER BY t.typname
        """,
    )

    return {
        "tables": [{"name": k, "columns": v} for k, v in sorted(tables.items())],
        "enums": [{"name": r["name"], "values": str(r["values"]).split(",")} for r in enums],
    }


#: Starting points that answer real questions, rather than `SELECT 1`.
SNIPPETS = [
    {
        "title": "Memory by category",
        "sql": "SELECT category::text, count(*) AS n\nFROM memory_chunks\nWHERE COALESCE(status,'active') = 'active'\nGROUP BY 1 ORDER BY n DESC;",
    },
    {
        "title": "Busiest projects",
        "sql": "SELECT project_name, count(*) AS conversations, max(started_at) AS last_seen\nFROM conversations\nWHERE project_name IS NOT NULL\nGROUP BY 1 ORDER BY conversations DESC LIMIT 20;",
    },
    {
        "title": "Chunks with no embedding",
        "sql": "SELECT mc.id, mc.category::text, left(mc.content, 80) AS content\nFROM memory_chunks mc\nWHERE NOT EXISTS (\n  SELECT 1 FROM embeddings e\n  WHERE e.source_type = 'memory_chunk' AND e.source_id = mc.id)\nLIMIT 50;",
    },
    {
        "title": "Supersede chains",
        "sql": "SELECT a.id AS older, left(a.content,60) AS older_text,\n       b.id AS newer, left(b.content,60) AS newer_text\nFROM memory_chunks a\nJOIN memory_chunks b ON b.id = a.superseded_by\nLIMIT 50;",
    },
    {
        "title": "Token spend by day",
        "sql": "SELECT date_trunc('day', started_at)::date AS day,\n       sum(token_count_in) AS tokens_in,\n       sum(token_count_out) AS tokens_out\nFROM conversations\nWHERE started_at > now() - interval '30 days'\nGROUP BY 1 ORDER BY 1;",
    },
]
