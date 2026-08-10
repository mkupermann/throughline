"""Single source of truth for every SQL statement Throughline runs.

Before this package existed, the GUI carried its own inline SQL in
``gui/page_views/*.py`` while the CLI, the MCP server and ``scripts/`` each
carried near-duplicates. That made three copies of the same query drift apart
and hid performance defects in the copy nobody profiled.

Everything here takes an open ``psycopg2`` connection as its first argument and
returns plain ``list[dict]`` / ``dict`` / scalars — no pandas, no Streamlit, no
FastAPI. Callers adapt to whatever shape they need.
"""

from __future__ import annotations

from . import (
    activity,
    console,
    conversations,
    curate,
    entities,
    find,
    health,
    memory,
    search,
    semantic,
    skills,
)
from ._exec import Row, execute, execute_batch, one, rows, scalar

__all__ = [
    "Row",
    "rows",
    "one",
    "scalar",
    "execute",
    "execute_batch",
    "activity",
    "console",
    "conversations",
    "curate",
    "entities",
    "find",
    "health",
    "memory",
    "search",
    "semantic",
    "skills",
]
