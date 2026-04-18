"""Unified command-line interface for Throughline.

Usage::

    throughline <command> [options]
    python -m throughline <command> [options]

Each subcommand is a thin wrapper around the matching script in
``scripts/`` of the source repository. Python scripts are imported and
their ``main()`` is called; shell scripts are invoked via ``subprocess``.

Run ``throughline --help`` for the full list of commands.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from throughline import __version__
from throughline.config import repo_root


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _ensure_scripts_on_path() -> Path:
    """Add ``<repo>/scripts`` to ``sys.path`` so scripts are importable.

    Returns the resolved repo root so shell-script commands can find their
    ``.sh`` files under ``scripts/``.
    """
    root = repo_root()
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        s = str(scripts_dir)
        if s not in sys.path:
            sys.path.insert(0, s)
    return root


def _call_script_main(module_name: str, argv: list[str] | None = None) -> int:
    """Import a script module and invoke its ``main()`` function.

    ``argv`` replaces ``sys.argv[1:]`` for the duration of the call so the
    script's own argparse usage works as if it had been invoked directly.
    Returns the exit code from the script (or 0 if it returns ``None``).
    """
    _ensure_scripts_on_path()
    try:
        module = __import__(module_name)
    except ImportError as e:
        print(f"ERROR: Could not import {module_name!r}: {e}", file=sys.stderr)
        print(
            "Make sure you are running from a Throughline source checkout "
            "(editable install) or that the scripts/ directory is present.",
            file=sys.stderr,
        )
        return 2

    if not hasattr(module, "main"):
        print(f"ERROR: {module_name!r} has no main() entrypoint.", file=sys.stderr)
        return 2

    saved_argv = sys.argv
    try:
        sys.argv = [module_name, *(argv or [])]
        result = module.main()
        if isinstance(result, int):
            return result
        return 0
    except SystemExit as e:
        # argparse inside scripts may call sys.exit — propagate cleanly
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        return code
    finally:
        sys.argv = saved_argv


def _run_shell_script(script_name: str, args: list[str]) -> int:
    """Execute a ``scripts/<script_name>`` shell script with the given args."""
    root = _ensure_scripts_on_path()
    script_path = root / "scripts" / script_name
    if not script_path.is_file():
        print(f"ERROR: Shell script not found: {script_path}", file=sys.stderr)
        return 2
    cmd = ["bash", str(script_path), *args]
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        print("ERROR: bash not available on this system.", file=sys.stderr)
        return 2


# --------------------------------------------------------------------------- #
# Subcommand handlers                                                         #
# --------------------------------------------------------------------------- #

def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest Claude Code JSONL sessions (or Windsurf plans with --windsurf)."""
    if args.windsurf:
        return _call_script_main("ingest_windsurf")
    return _call_script_main("ingest_sessions")


def cmd_scan_skills(args: argparse.Namespace) -> int:
    """Scan ``~/.claude/skills/`` and project-local skill directories."""
    return _call_script_main("scan_skills")


def cmd_scan_prompts(args: argparse.Namespace) -> int:
    """Scan ``CLAUDE.md`` and skill-based prompt templates."""
    return _call_script_main("scan_prompts")


def cmd_extract_memory(args: argparse.Namespace) -> int:
    """Extract structured memory chunks via Claude CLI."""
    return _call_script_main("extract_memory")


def cmd_generate_titles(args: argparse.Namespace) -> int:
    """Generate concise titles for conversations that are missing one."""
    return _call_script_main("generate_titles")


def cmd_embed(args: argparse.Namespace) -> int:
    """Generate vector embeddings for messages and memory chunks."""
    passthrough: list[str] = []
    if args.backend:
        passthrough += ["--backend", args.backend]
    if args.limit is not None:
        passthrough += ["--limit", str(args.limit)]
    if args.only:
        passthrough += ["--only", args.only]
    return _call_script_main("generate_embeddings", passthrough)


def cmd_search(args: argparse.Namespace) -> int:
    """Semantic search over memory chunks and messages."""
    passthrough: list[str] = [args.query]
    if args.backend:
        passthrough += ["--backend", args.backend]
    if args.limit is not None:
        passthrough += ["--limit", str(args.limit)]
    return _call_script_main("search_semantic", passthrough)


def cmd_reflect(args: argparse.Namespace) -> int:
    """Run the self-reflecting memory engine (dedup / contradictions / stale / consolidate)."""
    passthrough: list[str] = []
    if args.mode:
        passthrough += ["--mode", args.mode]
    if args.dry_run:
        passthrough += ["--dry-run"]
    if args.limit is not None:
        passthrough += ["--limit", str(args.limit)]
    return _call_script_main("reflect_memory", passthrough)


def cmd_gui(args: argparse.Namespace) -> int:
    """Launch the Streamlit GUI (`streamlit run gui/app.py`)."""
    root = _ensure_scripts_on_path()
    app = root / "gui" / "app.py"
    if not app.is_file():
        print(f"ERROR: GUI entrypoint not found: {app}", file=sys.stderr)
        return 2
    extra: list[str] = []
    if args.port:
        extra += ["--server.port", str(args.port)]
    cmd = ["streamlit", "run", str(app), *extra]
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        print(
            "ERROR: `streamlit` not installed. Install with: "
            "pip install -e . (core deps include Streamlit).",
            file=sys.stderr,
        )
        return 2


def cmd_install_hooks(args: argparse.Namespace) -> int:
    """Install Claude Code SessionStart hooks into ``~/.claude/settings.json``."""
    return _run_shell_script("install_hooks.sh", [])


def cmd_backup(args: argparse.Namespace) -> int:
    """Run a one-shot backup of the claude_memory database."""
    return _run_shell_script("backup.sh", [])


def cmd_version(args: argparse.Namespace) -> int:
    """Print the installed Throughline version."""
    print(f"throughline {__version__}")
    return 0


# --------------------------------------------------------------------------- #
# Parser construction                                                         #
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="throughline",
        description=(
            "Throughline — persistent long-term memory for Claude Code. "
            "Run `throughline <command> --help` for per-command options."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    # ingest
    p = sub.add_parser(
        "ingest",
        help="Ingest Claude Code JSONL sessions into the database.",
        description=(
            "Reads JSONL files from ~/.claude/projects/ and inserts "
            "conversations + messages into PostgreSQL. Use --windsurf to "
            "instead ingest Windsurf plans from ~/.windsurf/plans/."
        ),
    )
    p.add_argument(
        "--windsurf",
        action="store_true",
        help="Ingest Windsurf plans (~/.windsurf/plans/*.md) instead of Claude Code sessions.",
    )
    p.set_defaults(func=cmd_ingest)

    # scan-skills
    p = sub.add_parser(
        "scan-skills",
        help="Index all SKILL.md files in global + project skill directories.",
        description="Walks ~/.claude/skills/ and project-local .claude/skills/ directories.",
    )
    p.set_defaults(func=cmd_scan_skills)

    # scan-prompts
    p = sub.add_parser(
        "scan-prompts",
        help="Index CLAUDE.md files and skill prompts as reusable templates.",
    )
    p.set_defaults(func=cmd_scan_prompts)

    # extract-memory
    p = sub.add_parser(
        "extract-memory",
        help="Extract structured memory chunks via the Claude CLI (requires `claude`).",
    )
    p.set_defaults(func=cmd_extract_memory)

    # generate-titles
    p = sub.add_parser(
        "generate-titles",
        help="Generate concise titles for conversations missing a summary.",
    )
    p.set_defaults(func=cmd_generate_titles)

    # embed
    p = sub.add_parser(
        "embed",
        help="Generate vector embeddings (OpenAI or local Ollama).",
        description=(
            "Creates pgvector embeddings for memory_chunks and messages. "
            "Use --backend=ollama for a fully local setup."
        ),
    )
    p.add_argument("--backend", choices=["openai", "ollama", "auto"], default="auto",
                   help="Embeddings backend. Default: auto (OpenAI if key set, else Ollama).")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process N pending entries (useful for smoke tests).")
    p.add_argument("--only", choices=["memory_chunk", "message", "both"], default=None,
                   help="Restrict to a single source type.")
    p.set_defaults(func=cmd_embed)

    # search
    p = sub.add_parser(
        "search",
        help="Semantic search over memory chunks and messages.",
        description="Cosine-distance search via pgvector. Requires prior `throughline embed`.",
    )
    p.add_argument("query", help="Free-form search string.")
    p.add_argument("--backend", choices=["openai", "ollama", "auto"], default="auto",
                   help="Embeddings backend. Must match how embeddings were generated.")
    p.add_argument("--limit", type=int, default=None, help="Max number of results to return.")
    p.set_defaults(func=cmd_search)

    # reflect
    p = sub.add_parser(
        "reflect",
        help="Run the self-reflecting memory engine (dedup / contradictions / stale / consolidate).",
    )
    p.add_argument("--mode", choices=["dedup", "contradictions", "stale", "consolidate"],
                   default=None, help="Run a single mode instead of all four.")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write any changes to the database.")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on pair-comparisons per mode.")
    p.set_defaults(func=cmd_reflect)

    # gui
    p = sub.add_parser(
        "gui",
        help="Start the Streamlit GUI (requires `streamlit` on PATH).",
    )
    p.add_argument("--port", type=int, default=None,
                   help="Port for the Streamlit server (default: 8501).")
    p.set_defaults(func=cmd_gui)

    # install-hooks
    p = sub.add_parser(
        "install-hooks",
        help="Install SessionStart hooks into ~/.claude/settings.json.",
    )
    p.set_defaults(func=cmd_install_hooks)

    # backup
    p = sub.add_parser(
        "backup",
        help="Run a one-shot pg_dump backup of the claude_memory DB.",
    )
    p.set_defaults(func=cmd_backup)

    # version
    p = sub.add_parser(
        "version",
        help="Print the installed Throughline version and exit.",
    )
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
