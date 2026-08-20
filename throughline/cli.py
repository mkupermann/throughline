"""Unified command-line interface for Throughline.

Usage::

    throughline <command> [options]
    python -m throughline <command> [options]

Each subcommand is backed by an installable ``throughline.jobs`` module.
Top-level scripts remain direct-execution compatibility wrappers.

Run ``throughline --help`` for the full list of commands.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable

from throughline import __version__
from throughline.config import load_dotenv

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _call_script_main(module_name: str, argv: list[str] | None = None) -> int:
    """Import a packaged job module and invoke its ``main()`` function.

    ``argv`` replaces ``sys.argv[1:]`` for the duration of the call so the
    script's own argparse usage works as if it had been invoked directly.
    Returns the exit code from the script (or 0 if it returns ``None``).
    """
    try:
        module = importlib.import_module(f"throughline.jobs.{module_name}")
    except ImportError as e:
        print(f"ERROR: Could not import {module_name!r}: {e}", file=sys.stderr)
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
    from importlib.resources import files

    script_path = files("throughline") / "shell" / script_name
    if not script_path.is_file():
        print(f"ERROR: Shell script not found: {script_path}", file=sys.stderr)
        return 2
    cmd = ["bash", str(script_path), *args]
    try:
        return subprocess.call(cmd, env={**os.environ, "THROUGHLINE_PYTHON": sys.executable})
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
        present = [a for a in all_adapters() if a.is_present()]
        if not present:
            print(
                "No sources present — nothing ingested. Run 'throughline ingest --list-sources' to see expected paths."
            )
            return 1
        results = run_many(present)
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
            f"Unknown source: {name!r}. Run `throughline ingest --list-sources` to see the available adapters.\n"
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


def cmd_ask(args: argparse.Namespace) -> int:
    """Answer a question from the stored history, with citations."""
    from throughline import ask as _ask
    from throughline.status import _connect

    conn = _connect()
    if conn is None:
        print("Cannot reach PostgreSQL. Try `throughline doctor --category postgres`.")
        return 1
    try:
        result = _ask.answer(
            conn,
            args.question,
            # None means "whatever the module's measured default is", so the
            # CLI cannot drift from it silently.
            top_k=args.top_k if args.top_k is not None else _ask.DEFAULT_TOP_K,
            project=args.project,
            model=args.model,
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.text else 1

    if result.degraded and not result.text:
        print(result.degraded)
        # Sources without an answer still beat nothing: the reader can go and
        # read them, which is what they would have done before this command
        # existed.
        if result.sources:
            print()
            _print_sources(result.sources)
        return 1

    print(result.text)
    if result.cited:
        print()
        _print_sources(result.cited)
    elif result.sources:
        # An uncited answer is unverifiable, and silence about that would hide
        # exactly the failure this command must not have.
        print()
        print("(no citations — treat this answer as unverified)")
        _print_sources(result.sources[:3])
    return 0


def _print_sources(sources) -> None:
    print("Sources:")
    for s in sources:
        where = s.project or "—"
        print(f"  [{s.n}] {s.ref}  ·  {where}")


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


def cmd_migrate(args: argparse.Namespace) -> int:
    """Apply packaged SQL migrations, or report their status."""
    passthrough: list[str] = []
    if args.status:
        passthrough.append("--status")
    if args.dry_run:
        passthrough.append("--dry-run")
    return _call_script_main("migrate", passthrough)


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the web UI and its JSON API from one process on one port."""
    try:
        from throughline.api.server import serve
        from throughline.api.settings import RemoteBindRefused
    except ImportError as exc:
        print(
            f"ERROR: the API server could not be imported ({exc}).\nReinstall with: pip install -e .",
            file=sys.stderr,
        )
        return 2
    try:
        return serve(
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
    except RemoteBindRefused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def cmd_install_hooks(args: argparse.Namespace) -> int:
    """Install Claude Code SessionStart hooks into ``~/.claude/settings.json``."""
    return _run_shell_script("install_hooks.sh", [])


def cmd_backup(args: argparse.Namespace) -> int:
    """Run a one-shot backup of the Throughline database."""
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


def cmd_migrate_peer(args: argparse.Namespace) -> int:
    """Apply pending migrations to this database and to a replicating peer."""
    passthrough: list[str] = ["--peer-url", args.peer_url]
    if args.dry_run:
        passthrough.append("--dry-run")
    return _call_script_main("migrate_peer", passthrough)


def cmd_tunnel(args: argparse.Namespace) -> int:
    """Hold open a loopback-only link to the other machine's PostgreSQL."""
    passthrough: list[str] = ["--host", args.host, "--user", args.user]
    for flag, value in (
        ("--peer-port", args.peer_port),
        ("--local-port", args.local_port),
        ("--bridge-port", args.bridge_port),
    ):
        passthrough += [flag, str(value)]
    if args.identity:
        passthrough += ["--identity", args.identity]
    if args.show:
        passthrough.append("--print")
    return _call_script_main("tunnel", passthrough)


def cmd_consolidate(args: argparse.Namespace) -> int:
    """Move this database's contents into another Throughline database."""
    passthrough: list[str] = []
    if args.target_url:
        passthrough += ["--target-url", args.target_url]
    if args.export_to:
        passthrough += ["--export-to", args.export_to]
    if args.from_dump:
        passthrough += ["--from-dump", args.from_dump]
    if args.source_url:
        passthrough += ["--source-url", args.source_url]
    if args.dump_file:
        passthrough += ["--dump-file", args.dump_file]
    if args.dry_run:
        passthrough.append("--dry-run")
    return _call_script_main("consolidate", passthrough)


def cmd_export_markdown(args: argparse.Namespace) -> int:
    """Export the stored corpus as a Markdown vault, one folder per project."""
    passthrough: list[str] = ["--out", args.out] if args.out else []
    if args.project:
        passthrough += ["--project", args.project]
    if args.since:
        passthrough += ["--since", args.since]
    if args.include_generated:
        passthrough.append("--include-generated")
    if args.no_memory:
        passthrough.append("--no-memory")
    if args.redact:
        passthrough.append("--redact")
    passthrough += ["--tool-output", str(args.tool_output)]
    passthrough += ["--split-bytes", str(args.split_bytes)]
    return _call_script_main("export_markdown", passthrough)


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

    from throughline.doctor import format_human as doctor_format
    from throughline.doctor import run_doctor

    cats = args.category if getattr(args, "category", None) else None
    report = run_doctor(categories=cats)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2 if args.pretty else None, sort_keys=True))
        return 0
    print(doctor_format(report, color=not args.no_color))
    return 0 if report.fails == 0 else 1


def cmd_conflicts(args: argparse.Namespace) -> int:
    """Detect cross-tool memory conflicts.

    Three detection strategies run by default; ``--kind`` (repeatable)
    narrows. Exit codes:
        0  no conflicts found (or --json mode)
        1  one or more conflicts surfaced
        2  DB unreachable in non-JSON mode (matches `status`)
    """
    import json

    from throughline.conflicts import find_conflicts
    from throughline.conflicts import format_human as conflicts_format

    kinds = args.kind if getattr(args, "kind", None) else None
    report = find_conflicts(
        project=args.project,
        kinds=kinds,
        since_days=args.since_days,
        min_similarity=args.min_similarity,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2 if args.pretty else None, sort_keys=True, default=str))
        return 0
    print(conflicts_format(report))
    if not report.db_reachable:
        return 2
    if report.error and not report.conflicts:
        # Every strategy failed — don't let that masquerade as a clean run.
        return 2
    return 0 if not report.conflicts else 1


# --------------------------------------------------------------------------- #
# Parser construction                                                         #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="throughline",
        description=(
            "Throughline — one local memory layer for every AI coding CLI. "
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
            "Creates pgvector embeddings for memory_chunks and messages. Use --backend=ollama for a fully local setup."
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

    # ask
    p = sub.add_parser(
        "ask",
        help="Ask a question about your history and get a cited answer.",
        description=(
            "Retrieves the nearest records with pgvector, then has a model "
            "answer from them and cite what it used. Needs embeddings "
            "(`throughline embed`) and an answering model — Ollama, any "
            "OpenAI-compatible server, or the `claude` CLI, whichever is "
            "found first. `throughline doctor` reports which one that is."
        ),
    )
    p.add_argument("question", help="A question, in any language.")
    p.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="How many records to retrieve (default: throughline.ask.DEFAULT_TOP_K).",
    )
    p.add_argument("--project", default=None, help="Restrict retrieval to one project.")
    p.add_argument(
        "--model",
        default=None,
        help="Model for the answer. Default: whatever the configured backend resolves to.",
    )
    p.add_argument("--json", action="store_true", help="Emit the answer and sources as JSON.")
    p.set_defaults(func=cmd_ask)

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

    # migrate
    p = sub.add_parser("migrate", help="Apply packaged database migrations.")
    p.add_argument("--status", action="store_true", help="List applied and pending migrations.")
    p.add_argument("--dry-run", action="store_true", help="List pending migrations without applying them.")
    p.set_defaults(func=cmd_migrate)

    # serve
    p = sub.add_parser(
        "serve",
        help="Serve the web UI and JSON API on one port (default: 127.0.0.1:8790).",
    )
    p.add_argument(
        "--host",
        default=None,
        help=(
            "Bind address (default 127.0.0.1). Non-loopback is refused unless "
            "THROUGHLINE_ALLOW_REMOTE=1 — the API has no authentication."
        ),
    )
    p.add_argument("--port", type=int, default=None, help="Port (default: 8790).")
    p.add_argument("--reload", action="store_true", help="Auto-reload on source changes (development).")
    p.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="uvicorn log level.",
    )
    p.set_defaults(func=cmd_serve)

    # install-hooks
    p = sub.add_parser(
        "install-hooks",
        help="Install SessionStart hooks into ~/.claude/settings.json.",
    )
    p.set_defaults(func=cmd_install_hooks)

    # backup
    p = sub.add_parser(
        "backup",
        help="Run a one-shot pg_dump backup of the Throughline database.",
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
    p.add_argument(
        "--include-conversations", action="store_true", help="Also pull project_names from the conversations table."
    )
    p.add_argument("--dry-run", action="store_true", help="Preview what would be inserted; do not write.")
    p.set_defaults(func=cmd_backfill_projects)

    # migrate-peer
    p = sub.add_parser(
        "migrate-peer",
        help="Apply pending migrations to this database and to a replicating peer.",
        description=(
            "Logical replication carries rows, never DDL: a column added on "
            "one node stops the other's subscription at the first row that "
            "has it. Both subscriptions are paused, both nodes are migrated, "
            "and replication resumes — including when a migration fails, "
            "because a pair left not replicating fails silently."
        ),
    )
    p.add_argument("--peer-url", required=True, help="Connection URL of the other node.")
    p.add_argument("--dry-run", action="store_true", help="Print the plan and change nothing.")
    p.set_defaults(func=cmd_migrate_peer)

    # tunnel
    p = sub.add_parser(
        "tunnel",
        help="Hold open a loopback-only link to another machine's PostgreSQL.",
        description=(
            "One SSH connection carries both directions, so only the machine "
            "being dialled needs an SSH server, and neither database is ever "
            "exposed to the network — both forwards bind to 127.0.0.1. Does "
            "not daemonise; see launchd/com.throughline-tunnel.plist."
        ),
    )
    p.add_argument("--host", required=True, help="The other machine, e.g. framework.fritz.box.")
    p.add_argument("--user", required=True, help="Login on the other machine.")
    p.add_argument("--peer-port", type=int, default=5433, help="Its PostgreSQL port (default: 5433).")
    p.add_argument("--local-port", type=int, default=5433, help="This machine's PostgreSQL port.")
    p.add_argument("--bridge-port", type=int, default=5434, help="Where each side reaches the other.")
    p.add_argument("--identity", default=None, help="SSH key to use.")
    p.add_argument("--print", action="store_true", dest="show", help="Print the command and exit.")
    p.set_defaults(func=cmd_tunnel)

    # consolidate
    p = sub.add_parser(
        "consolidate",
        help="Move this database's contents into another Throughline database.",
        description=(
            "One-way: dump the source, replace the target, then compare row "
            "counts across every table. The source is never modified and "
            "remains the fallback until the counts agree. Refuses a major "
            "version mismatch and an empty source."
        ),
    )
    p.add_argument("--target-url", default=None, help="Target connection URL. Its contents are replaced.")
    p.add_argument("--source-url", default=None, help="Source connection URL (default: the configured database).")
    p.add_argument(
        "--export-to",
        default=None,
        help="Write an archive plus its row counts and stop — for carrying a corpus to another machine.",
    )
    p.add_argument(
        "--from-dump",
        default=None,
        help="Restore an archive written by --export-to, then verify it against its counts.",
    )
    p.add_argument("--dump-file", default=None, help="Where to keep the archive (default: a temporary file).")
    p.add_argument("--dry-run", action="store_true", help="Report the plan and the counts; move nothing.")
    p.set_defaults(func=cmd_consolidate)

    # export-markdown
    p = sub.add_parser(
        "export-markdown",
        help="Export the corpus as a Markdown vault, one folder per project.",
        description=(
            "Writes the stored sessions to Markdown: one folder per "
            "project, sessions oldest first, each reproducing the prompt, "
            "the answer, every tool call, the shell commands, and a "
            "file:// link to every file that was created or changed. "
            "Machine-generated conversations are excluded unless "
            "--include-generated is given. Large projects split into "
            "monthly files so an editor can still open them."
        ),
    )
    p.add_argument(
        "--out",
        default=None,
        help="Destination directory (created if missing). Falls back to $THROUGHLINE_EXPORT_OUT.",
    )
    p.add_argument("--project", default=None, help="Export a single project instead of all.")
    p.add_argument("--since", default=None, help="Only sessions started on or after this date (YYYY-MM-DD).")
    p.add_argument("--include-generated", action="store_true", help="Also export machine-generated conversations.")
    p.add_argument(
        "--tool-output",
        type=int,
        default=0,
        help="Characters of each tool result to include, collapsed (default: 0, omit them).",
    )
    p.add_argument(
        "--split-bytes", type=int, default=1_500_000, help="Split a project into dated parts above this size."
    )
    p.add_argument("--no-memory", action="store_true", help="Skip the per-project Memory.md file.")
    p.add_argument(
        "--redact",
        action="store_true",
        help="Run every exported text through the PII pass (keys, tokens, emails, home paths).",
    )
    p.set_defaults(func=cmd_export_markdown)

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
    p.add_argument("--dry-run", action="store_true", help="Preview changes; do not write.")
    p.add_argument("--limit", type=int, default=None, help="Cap on number of files processed.")
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

    # conflicts
    p = sub.add_parser(
        "conflicts",
        help="Detect cross-tool memory conflicts (supersession, semantic, stale drift).",
        description=(
            "Surfaces cases where the same project's memory was touched by "
            "two different AI CLIs in contradictory ways. Three detection "
            "strategies run by default: (1) chunks explicitly superseded "
            "across tool boundaries, (2) semantic near-duplicates from "
            "different tools where the newer side contains contradiction "
            "markers, (3) stale chunks from one tool while another tool "
            "has newer chunks for the same project. Read-only; uses "
            "existing schema (memory_chunks, embeddings, conversations)."
        ),
    )
    p.add_argument("--project", default=None, help="Restrict to a single project_name.")
    p.add_argument(
        "--kind",
        action="append",
        choices=["supersession", "semantic", "stale_drift"],
        help="Restrict to one or more detection strategies (repeatable). Default: run all three.",
    )
    p.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Stale-drift cutoff in days (default 30; chunks older than this with 0 accesses qualify).",
    )
    p.add_argument(
        "--min-similarity",
        type=float,
        default=0.85,
        help="Semantic cosine similarity threshold for cross-tool duplicates (default 0.85).",
    )
    p.add_argument("--json", action="store_true", help="Emit a JSON object on stdout.")
    p.add_argument("--pretty", action="store_true", help="With --json, indent the output.")
    p.set_defaults(func=cmd_conflicts)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Load a local .env (PGUSER / PGPASSWORD / …) before anything touches the
    # DB config. Never overrides real environment variables, so shell- or
    # Docker-provided settings still win.
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
