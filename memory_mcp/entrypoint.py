"""Console entry point that keeps ``--help`` dependency-free."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    """Start the MCP server, or describe its stdio invocation."""
    if argv is None:
        argv = sys.argv[1:]
    if argv in (["-h"], ["--help"]):
        print("Memory MCP server (stdio transport).")
        print("Run without arguments from an MCP client configuration.")
        return
    from memory_mcp.server import main as run_server

    run_server(argv)
