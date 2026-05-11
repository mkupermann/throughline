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
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

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
    """Ingest conversations from one or more local AI sources.

    Source selection precedence:
      1. ``--list-sources``  → print and exit.
      2. ``--all``           → run every adapter whose data directory exists.
      3. ``--source NAME``   → run that adapter only.
      4. Legacy ``--windsurf`` / ``--hermes`` flags → equivalent to
         ``--source windsurf`` / ``--source hermes``.
      5. Default              → run the Claude Code adapter.
    """
    from throughline.adapters import all_adapters, get_adapter
    from throughline.adapters.writer import run_adapter, run_many

    if args.list_sources:
        print(f"{'NAME':<14} {'PRESENT':<8} HOME")
        print("-" * 70)
        for a in all_adapters():
            present = "yes" if a.is_present() else "no"
            print(f"{a.name:<14} {present:<8} {a.home}")
        return 0

    if args.all:
        results = run_many([a for a in all_adapters() if a.is_present()])
        return 0 if all(r.errors == 0 for r in results) else 1

    name = args.source
    if not name:
        if args.windsurf:
            name = "windsurf"
        elif args.hermes:
            name = "hermes"
        else:
            name = "claude_code"

    adapter = get_adapter(name)
    if adapter is None:
        sys.stderr.write(
            f"Unknown source: {name!r}. Run `throughline ingest --list-sources` "
            f"to see the available adapters.\n"
        )
        return 2
    summary = run_adapter(adapter)
    return 0 if summary.errors == 0 else 1


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
            "ERROR: `streamlit` not installed. Install with: " "pip install -e . (core deps include Streamlit).",
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


def cmd_backfill_projects(args: argparse.Namespace) -> int:
    """Insert one projects row for each project_name observed in memory."""
    passthrough: list[str] = []
    if args.include_conversations:
        passthrough.append("--include-conversations")
    if args.dry_run:
        passthrough.append("--dry-run")
    return _call_script_main("backfill_projects", passthrough)


def cmd_repair_conversations(args: argparse.Namespace) -> int:
    """Re-read JSONL files and repair conversations.project_path / token counts."""
    passthrough: list[str] = []
    if args.dry_run:
        passthrough.append("--dry-run")
    if args.limit is not None:
        passthrough += ["--limit", str(args.limit)]
    return _call_script_main("repair_conversations", passthrough)


def cmd_status(args: argparse.Namespace) -> int:
    """Print a health snapshot of the memory DB.

    Plain text by default; ``--json`` emits a JSON object on stdout.
    Exit code is 0 if the DB was reachable, 2 if not — except in
    ``--json`` mode, where exit is always 0 so machine consumers can
    parse the payload (which carries ``db_reachable`` itself).
    """
    import json

    from throughline.status import collect_status, format_human

    payload = collect_status()
    if args.json:
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    print(format_human(payload))
    return 0 if payload.get("db_reachable") else 2


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run install-time diagnostics across Python, Postgres, adapters, embeddings, schedule.

    Exit codes:
        0  no failing checks (warnings allowed)
        1  one or more FAIL checks
        2  reserved for fatal-startup errors (never raised here)

    With ``--json`` the report is emitted as a JSON object on stdout and
    the exit code is always 0 so machine consumers (CI, health endpoints)
    can parse the payload — the ``summary.fail`` field tells them whether
    anything is wrong.
    """
    import json

    from throughline.doctor import run_doctor, format_human as doctor_format

    cats = args.category if getattr(args, "category", None) else None
    report = run_doctor(categories=cats)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2 if args.pretty else None, sort_keys=True))
        return 0
    print(doctor_format(report, color=not args.no_color))
    return 0 if report.fails == 0 else 1


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
        help="Ingest conversations from local AI tools (Claude Code, Hermes, Codex, …).",
        description=(
            "Pulls conversations from any registered source adapter into the "
            "shared `conversations` + `messages` tables. Defaults to the "
            "Claude Code adapter (~/.claude/projects/). Use --source to pick "
            "another, --all to run every adapter whose data directory exists, "
            "or --list-sources to see what's registered on this machine."
        ),
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--source",
        metavar="NAME",
        help="Run a single adapter by name (e.g. claude_code, hermes, codex, continue).",
    )
    src.add_argument(
        "--all",
        action="store_true",
        help="Run every adapter whose data directory exists on this machine.",
    )
    src.add_argument(
        "--list-sources",
        action="store_true",
        help="Print the available source adapters and whether each is present, then exit.",
    )
    # Legacy compatibility aliases — equivalent to --source <name>.
    src.add_argument(
        "--windsurf",
        action="store_true",
        help="Deprecated alias for --source windsurf.",
    )
    src.add_argument(
        "--hermes",
        action="store_true",
        help="Deprecated alias for --source hermes.",
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
    p.add_argument(
        "--backend",
        choices=["openai", "ollama", "auto"],
        default="auto",
        help="Embeddings backend. Default: auto (OpenAI if key set, else Ollama).",
    )
    p.add_argument("--limit", type=int, default=None, help="Only process N pending entries (useful for smoke tests).")
    p.add_argument(
        "--only", choices=["memory_chunk", "message", "both"], default=None, help="Restrict to a single source type."
    )
    p.set_defaults(func=cmd_embed)

    # search
    p = sub.add_parser(
        "search",
        help="Semantic search over memory chunks and messages.",
        description="Cosine-distance search via pgvector. Requires prior `throughline embed`.",
    )
    p.add_argument("query", help="Free-form search string.")
    p.add_argument(
        "--backend",
        choices=["openai", "ollama", "auto"],
        default="auto",
        help="Embeddings backend. Must match how embeddings were generated.",
    )
    p.add_argument("--limit", type=int, default=None, help="Max number of results to return.")
    p.set_defaults(func=cmd_search)

    # reflect
    p = sub.add_parser(
        "reflect",
        help="Run the self-reflecting memory engine (dedup / contradictions / stale / consolidate).",
    )
    p.add_argument(
        "--mode",
        choices=["dedup", "contradictions", "stale", "consolidate"],
        default=None,
        help="Run a single mode instead of all four.",
    )
    p.add_argument("--dry-run", action="store_true", help="Don't write any changes to the database.")
    p.add_argument("--limit", type=int, default=None, help="Cap on pair-comparisons per mode.")
    p.set_defaults(func=cmd_reflect)

    # gui
    p = sub.add_parser(
        "gui",
        help="Start the Streamlit GUI (requires `streamlit` on PATH).",
    )
    p.add_argument("--port", type=int, default=None, help="Port for the Streamlit server (default: 8501).")
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

    # backfill-projects
    p = sub.add_parser(
        "backfill-projects",
        help="Populate the projects table from observed project_name values.",
        description=(
            "For each distinct project_name found in memory_chunks (and "
            "optionally conversations), insert a row into the projects "
            "table. Existing rows are never modified — manually-curated "
            "descriptions, contacts, decisions are safe. Idempotent: "
            "re-running adds only the genuinely new names."
        ),
    )
    p.add_argument("--include-conversations", action="store_true",
                   help="Also pull project_names from the conversations table.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview what would be inserted; do not write.")
    p.set_defaults(func=cmd_backfill_projects)

    # repair-conversations
    p = sub.add_parser(
        "repair-conversations",
        help="Re-read JSONL files; fix project_path + token counts on existing rows.",
        description=(
            "Repairs two ingest bugs in existing conversations rows: "
            "(1) project_path was hyphen-mangled (claude-memory-db → "
            "claude/memory/db); (2) token_count_in / token_count_out were "
            "never populated. Reads each file referenced via ingestion_log "
            "and updates the matching row. Idempotent."
        ),
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Preview changes; do not write.")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on number of files processed.")
    p.set_defaults(func=cmd_repair_conversations)

    # status
    p = sub.add_parser(
        "status",
        help="Health snapshot of the memory DB (table counts, embedding coverage, …).",
        description=(
            "Reports DB reachability, schema version, table row counts, "
            "memory-chunk totals by category, embedding coverage, and the "
            "timestamps of the most recent extraction and reflection runs. "
            "Use --json for a machine-readable payload."
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit a JSON object on stdout instead of human text.")
    p.add_argument("--pretty", action="store_true", help="With --json, indent the output (default: single line).")
    p.set_defaults(func=cmd_status)

    # doctor
    p = sub.add_parser(
        "doctor",
        help="Run install diagnostics: Python, Postgres+pgvector, adapters, embeddings, schedule.",
        description=(
            "Diagnoses install / runtime issues across five categories: "
            "python (interpreter + required packages), postgres "
            "(reachability + pgvector + schema), adapters (which AI tool "
            "home directories exist), embeddings (OpenAI key or Ollama "
            "reachable), schedule (launchd plists on macOS / systemd "
            "timers on Linux). Read-only: never writes, mutates, or "
            "installs anything. Exit 0 if no FAIL checks (warnings allowed); "
            "exit 1 otherwise. Use --json for a machine-readable payload."
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit a JSON object on stdout instead of human text.")
    p.add_argument("--pretty", action="store_true", help="With --json, indent the output.")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors in human output.")
    p.add_argument(
        "--category",
        action="append",
        choices=["python", "postgres", "adapters", "embeddings", "schedule"],
        help="Restrict to one or more categories (repeatable). Default: run all.",
    )
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
