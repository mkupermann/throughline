"""Connection pooling and request-scoped database dependencies.

The Streamlit app opened one long-lived connection and reconnected when it
died. A server handling concurrent requests cannot do that, so connections
come from a ``ThreadedConnectionPool``.

psycopg2's pool rather than psycopg3's ``psycopg_pool``: every query in
``throughline.queries`` is written against psycopg2 (``RealDictCursor``,
``execute_values``, ``Json``). Running two drivers to gain an async pool
would buy nothing here — the endpoints are short reads against a local
database, and FastAPI runs sync handlers in a threadpool anyway.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.pool import PoolError, ThreadedConnectionPool

from throughline.config import get_db_config

from .settings import Settings

log = logging.getLogger("throughline.api")

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


class DatabaseUnavailable(RuntimeError):
    """The database could not be reached. Surfaced as a 503, never a 500."""


def init_pool(settings: Settings) -> None:
    """Create the global pool. Safe to call twice; the second call is a no-op.

    A database that is down at startup is not fatal: the pool is created
    lazily on first use instead, so the server still boots and every endpoint
    reports 503 with a useful message rather than the process refusing to
    start.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        try:
            _pool = ThreadedConnectionPool(
                settings.pool_min, settings.pool_max, **get_db_config()
            )
        except psycopg2.Error as exc:
            log.warning("database unreachable at startup, will retry per-request: %s", exc)
            _pool = None


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            finally:
                _pool = None


def _get_or_create_pool(settings: Settings) -> ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            try:
                _pool = ThreadedConnectionPool(
                    settings.pool_min, settings.pool_max, **get_db_config()
                )
            except psycopg2.Error as exc:
                raise DatabaseUnavailable(str(exc)) from exc
        return _pool


@contextmanager
def connection(settings: Settings) -> Iterator["psycopg2.extensions.connection"]:
    """Yield a pooled connection, returning it on the way out.

    A connection that has gone stale (server restarted, laptop slept) is
    discarded rather than handed back to the pool, so one dead socket cannot
    poison every subsequent request.
    """
    pool = _get_or_create_pool(settings)
    try:
        conn = pool.getconn()
    except (PoolError, psycopg2.Error) as exc:
        raise DatabaseUnavailable(str(exc)) from exc

    broken = False
    try:
        yield conn
    except psycopg2.Error:
        broken = True
        raise
    finally:
        try:
            if broken or conn.closed:
                pool.putconn(conn, close=True)
            else:
                # Reads leave an idle transaction open; end it so the next
                # borrower does not inherit a stale snapshot.
                conn.rollback()
                pool.putconn(conn)
        except Exception:  # pragma: no cover — pool teardown races
            pass
