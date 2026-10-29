"""Throughline — one memory layer for every local AI CLI.

A local-first, self-reflecting memory database that ingests session files
from Claude Code, OpenAI Codex CLI, Hermes Agent, Continue.dev, Cline and
Windsurf into a shared Postgres+pgvector store, extracts structured memory
chunks, and exposes the unified history back to any of them — as a Claude
Code skill, an MCP server (any MCP-aware client), or raw context injected
at session start.

This package is a thin CLI wrapper around the scripts in the ``scripts/``
directory of the source repository. The scripts remain directly executable;
this package simply unifies them under a single ``throughline`` entrypoint.
"""

from __future__ import annotations

__version__ = "0.3.0"
__all__ = ["__version__"]
