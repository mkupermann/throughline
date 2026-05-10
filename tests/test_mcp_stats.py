"""Tests for the ``memory.stats`` MCP tool.

We don't go through the FastMCP transport here — we exercise the wrapped
function directly, which is what ``tool_to_call(...)`` resolves to. The
real protocol-level test would need an MCP client; the value of this
test is that the parent re-uses ``throughline.status.collect_status`` so
the payload contract is enforced in one place.
"""
from __future__ import annotations

import pytest


def test_memory_stats_tool_is_registered():
    from memory_mcp import server
    # FastMCP wraps the function; in either form, the symbol exists.
    assert hasattr(server, "stats"), \
        "memory_mcp.server.stats must exist for the MCP tool wiring"


def test_memory_stats_returns_payload_when_db_unreachable(monkeypatch):
    """Connect failure must surface as ``db_reachable=False`` rather than
    a 500-style error from the MCP transport."""
    from memory_mcp import server

    monkeypatch.setattr(server, "connect", lambda: None)
    # collect_status's fallback path will fire because conn is None and
    # then it tries to open one itself — also force that to fail.
    from throughline import status as st
    monkeypatch.setattr(st, "_connect", lambda: None)

    payload = server.stats()
    assert isinstance(payload, dict)
    assert payload.get("db_reachable") is False
    assert "table_row_counts" in payload
    assert "chunks_by_category" in payload


def test_memory_stats_payload_has_stable_keys(monkeypatch):
    from memory_mcp import server
    from throughline import status as st

    monkeypatch.setattr(server, "connect", lambda: None)
    monkeypatch.setattr(st, "_connect", lambda: None)

    payload = server.stats()
    expected = {
        "db_reachable", "captured_at", "schema_version", "error",
        "table_row_counts", "chunks_total", "chunks_by_category",
        "embedding_coverage_pct", "last_extraction_at",
        "last_reflection_at", "contradictions_outstanding",
        "projects_count", "version",
    }
    missing = expected - set(payload)
    assert not missing, f"memory.stats payload missing keys: {missing}"
