"""Throughline — Persistent long-term memory for Claude Code.

A local-first, self-reflecting memory database that ingests Claude Code JSONL
sessions, extracts insights, and gives Claude its own memory to query across
sessions.

This package is a thin CLI wrapper around the scripts in the ``scripts/``
directory of the source repository. The scripts remain directly executable;
this package simply unifies them under a single ``throughline`` entrypoint.
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__"]
