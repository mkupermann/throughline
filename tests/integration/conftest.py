"""
Pytest fixtures for integration tests that need a real PostgreSQL database.

The fixtures assume that either:

- a ready-to-use Postgres is reachable via the standard ``PG*`` environment
  variables (the default on CI, where we run a ``services: postgres`` job), or
- the caller brought up ``docker-compose up postgres`` on localhost first.

Every test gets a freshly created, schema-loaded database with a unique name,
which is dropped afterwards. This keeps tests hermetic even when run against a
shared server.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:  # pragma: no cover - psycopg2 is always required in integration CI
    psycopg2 = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_SQL = REPO_ROOT / "sql" / "schema.sql"

# Ensure scripts/ is importable by the tests themselves.
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _admin_dsn() -> dict:
    """DSN for connecting to the ``postgres`` maintenance database."""
    return {
        "dbname": os.environ.get("PGADMINDB", "postgres"),
        "user": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
        "password": os.environ.get("PGPASSWORD", "postgres"),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
    }


def _db_available() -> bool:
    """Cheap probe: can we even connect?"""
    if psycopg2 is None:
        return False
    try:
        conn = psycopg2.connect(**_admin_dsn(), connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def _pg_available() -> bool:
    available = _db_available()
    if not available:
        pytest.skip(
            "No reachable PostgreSQL — set PG* env vars or run "
            "'docker compose up -d postgres' before 'pytest -m integration'.",
            allow_module_level=False,
        )
    return available


@pytest.fixture
def test_db(_pg_available) -> Iterator[dict]:
    """
    Create a fresh database, apply ``sql/schema.sql``, yield a connection dict,
    then drop the database afterwards.
    """
    db_name = f"tl_it_{uuid.uuid4().hex[:12]}"

    admin = psycopg2.connect(**_admin_dsn())
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    admin.close()

    conn_info = {
        "dbname": db_name,
        "user": _admin_dsn()["user"],
        "password": _admin_dsn()["password"],
        "host": _admin_dsn()["host"],
        "port": _admin_dsn()["port"],
    }

    # Apply schema.
    conn = psycopg2.connect(**conn_info)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.close()

    try:
        yield conn_info
    finally:
        admin = psycopg2.connect(**_admin_dsn())
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with admin.cursor() as cur:
            # Kill any stragglers before dropping.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
        admin.close()


@pytest.fixture
def db_env(monkeypatch, test_db) -> dict:
    """
    Wire the PG* env vars that scripts pick up, pointing at ``test_db``.
    Returns the same dict for convenience.
    """
    monkeypatch.setenv("PGDATABASE", test_db["dbname"])
    monkeypatch.setenv("PGUSER", test_db["user"])
    monkeypatch.setenv("PGHOST", test_db["host"])
    monkeypatch.setenv("PGPORT", str(test_db["port"]))
    if test_db.get("password"):
        monkeypatch.setenv("PGPASSWORD", test_db["password"])
    return test_db


@pytest.fixture
def db_connection(test_db) -> Iterator["psycopg2.extensions.connection"]:
    """A plain psycopg2 connection to the freshly created test DB."""
    conn = psycopg2.connect(**test_db)
    try:
        yield conn
    finally:
        conn.close()
