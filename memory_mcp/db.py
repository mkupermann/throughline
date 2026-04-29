"""DB connection factory for the MCP server.

Honours the standard libpq env vars (PGHOST/PGPORT/PGDATABASE/PGUSER/
PGPASSWORD) so the server works inside Docker, in CI, or against a local
DB without any code edits. Falls back to the same defaults the rest of
the codebase uses.
"""
from __future__ import annotations

import os
import psycopg2


def db_config() -> dict:
    cfg: dict = {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "dbname": os.environ.get("PGDATABASE", "claude_memory"),
        "user": os.environ.get("PGUSER", "mkupermann"),
    }
    pw = os.environ.get("PGPASSWORD")
    if pw:
        cfg["password"] = pw
    return cfg


def connect():
    return psycopg2.connect(**db_config())
