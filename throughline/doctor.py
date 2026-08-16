"""Throughline doctor — diagnose install / runtime issues.

``throughline doctor`` runs a structured battery of checks across six
categories and reports each as PASS / WARN / FAIL with a one-line remedy
hint. Exit codes:

  0  all checks passed (warnings allowed)
  1  one or more FAIL checks
  2  fatal — couldn't even start the diagnostic (rare)

The doctor is deliberately read-only. It never writes to the DB, never
mutates config, never installs anything. It tells you what's wrong; you
decide what to do.

Categories
----------
1. **Python environment** — interpreter version, required packages.
2. **PostgreSQL + pgvector** — server reachable, extension installed,
   schema present, schema version.
3. **Source adapters** — for each registered adapter, does its expected
   home directory exist and contain anything readable?
4. **Embeddings backend** — Ollama reachable (if configured), or
   OpenAI key present (if configured), or neither (then warn).
5. **Scheduled jobs** — launchd plists on macOS or systemd timers on
   Linux. Warn if not installed; the user may run ingest manually.
6. **Archive integrity** — the database's own consistency, and whether a
   recent backup exists. Separate from the rest because this store is the
   only surviving copy of most of what it holds: the source CLIs rotate
   their transcripts away, so 91% of ingested Claude Code sessions no
   longer exist on disk and nothing else could reconstruct them.

Each check returns a ``CheckResult`` dataclass. The CLI module formats
them; the JSON mode emits the raw list so other tools (CI, health
endpoints) can consume it.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str                         # short stable identifier ("python_version")
    category: str                     # one of the six categories above
    status: str                       # "pass" | "warn" | "fail"
    message: str                      # human-readable, one line
    remedy: str | None = None         # optional one-line fix hint
    details: dict[str, Any] = field(default_factory=dict)  # structured extras

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def fails(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def warns(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    @property
    def passes(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "summary": {"pass": self.passes, "warn": self.warns, "fail": self.fails},
        }


# ---------------------------------------------------------------------------
# Individual checks (each returns a CheckResult; none of them raise)
# ---------------------------------------------------------------------------


def _check(name: str, category: str) -> Callable:
    """Decorator: wrap a check fn so any exception becomes a FAIL result.

    Doctor must never crash; an exception inside one check should still let
    the rest run.
    """

    def deco(fn: Callable[[], CheckResult]) -> Callable[[], CheckResult]:
        def wrapped() -> CheckResult:
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 — explicitly catching all
                return CheckResult(
                    name=name,
                    category=category,
                    status="fail",
                    message=f"check raised {type(e).__name__}: {e}",
                    remedy="file a bug if this persists; share the JSON output",
                )

        wrapped.__name__ = fn.__name__
        return wrapped

    return deco


# --- 1. Python environment --------------------------------------------------


@_check("python_version", "python")
def check_python_version() -> CheckResult:
    major, minor = sys.version_info.major, sys.version_info.minor
    cur = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) < (3, 10):
        return CheckResult(
            "python_version", "python", "fail",
            f"Python {cur} found; throughline requires 3.10+",
            remedy="install Python 3.10 or newer (e.g. `brew install python@3.12`)",
            details={"current": cur, "required": ">=3.10"},
        )
    return CheckResult(
        "python_version", "python", "pass",
        f"Python {cur}", details={"current": cur},
    )


@_check("required_packages", "python")
def check_required_packages() -> CheckResult:
    # What `throughline serve` and the pipeline actually import. The
    # Streamlit/pandas/plotly stack left with the old GUI.
    required = ["psycopg2", "fastapi", "uvicorn", "yaml"]
    missing: list[str] = []
    for pkg in required:
        try:
            __import__(pkg)
        except Exception:
            missing.append(pkg)
    if missing:
        return CheckResult(
            "required_packages", "python", "fail",
            f"missing Python packages: {', '.join(missing)}",
            remedy=f"pip install {' '.join(missing)}",
            details={"missing": missing},
        )
    return CheckResult(
        "required_packages", "python", "pass",
        "all required Python packages importable",
        details={"checked": required},
    )


@_check("optional_packages", "python")
def check_optional_packages() -> CheckResult:
    optional = {
        "mcp": "MCP server (memory_mcp/)",
        "openai": "OpenAI embeddings backend",
        "ollama": "Ollama embeddings backend",
        "anthropic": "memory extraction via Anthropic SDK",
    }
    missing = []
    for pkg, why in optional.items():
        try:
            __import__(pkg)
        except Exception:
            missing.append((pkg, why))
    if not missing:
        return CheckResult(
            "optional_packages", "python", "pass",
            "all optional packages importable",
        )
    return CheckResult(
        "optional_packages", "python", "warn",
        f"{len(missing)} optional packages not installed (see details)",
        remedy="install only what you actually use; none are required",
        details={"missing": [{"name": n, "purpose": w} for n, w in missing]},
    )


# --- 2. PostgreSQL + pgvector ----------------------------------------------


@_check("postgres_reachable", "postgres")
def check_postgres_reachable() -> CheckResult:
    from throughline.status import _connect

    conn = _connect()
    if conn is None:
        host = os.environ.get("PGHOST", "localhost")
        port = os.environ.get("PGPORT", "5432")
        db = os.environ.get("PGDATABASE", "throughline")
        return CheckResult(
            "postgres_reachable", "postgres", "fail",
            f"cannot connect to Postgres at {host}:{port}/{db}",
            remedy=(
                "start Postgres (e.g. `pg_ctl start` / `brew services start postgresql@16`) "
                "or `docker compose up -d`; check PGHOST/PGPORT/PGDATABASE env vars. "
                "After changing credentials on an existing Compose volume, run the "
                "credential-rotate command in docs/DEPLOYMENT.md before restarting web."
            ),
            details={"host": host, "port": port, "db": db},
        )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
    finally:
        conn.close()
    return CheckResult(
        "postgres_reachable", "postgres", "pass",
        f"Postgres reachable: {ver.split(',')[0]}",
        details={"server_version": ver},
    )


@_check("pgvector_installed", "postgres")
def check_pgvector() -> CheckResult:
    from throughline.status import _connect

    conn = _connect()
    if conn is None:
        return CheckResult(
            "pgvector_installed", "postgres", "warn",
            "skipped (Postgres not reachable)",
        )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'"
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return CheckResult(
            "pgvector_installed", "postgres", "fail",
            "pgvector extension not installed in this database",
            remedy=(
                "CREATE EXTENSION vector;  -- as a Postgres superuser; "
                "install pgvector first if needed (https://github.com/pgvector/pgvector)"
            ),
        )
    return CheckResult(
        "pgvector_installed", "postgres", "pass",
        f"pgvector {row[1]} installed",
        details={"version": row[1]},
    )


@_check("schema_present", "postgres")
def check_schema_present() -> CheckResult:
    from throughline.status import collect_status

    payload = collect_status()
    if not payload.get("db_reachable"):
        return CheckResult(
            "schema_present", "postgres", "warn",
            "skipped (Postgres not reachable)",
        )
    counts = payload.get("table_row_counts") or {}
    missing = [t for t in ("conversations", "messages", "memory_chunks") if t not in counts]
    if missing:
        return CheckResult(
            "schema_present", "postgres", "fail",
            f"core tables missing: {', '.join(missing)}",
            remedy="run the schema migrations (`docker compose up -d` auto-deploys, or `psql -f db/schema.sql`)",
            details={"missing_tables": missing},
        )
    sv = payload.get("schema_version")
    return CheckResult(
        "schema_present", "postgres", "pass",
        f"core schema present (version: {sv or 'unknown'})",
        details={"schema_version": sv, "table_counts": counts},
    )


# --- 3. Source adapters -----------------------------------------------------


@_check("source_adapters", "adapters")
def check_source_adapters() -> CheckResult:
    try:
        from throughline.adapters import all_adapters
    except Exception as e:
        return CheckResult(
            "source_adapters", "adapters", "fail",
            f"failed to import adapter registry: {e}",
            remedy="check that throughline is installed (`pip install -e .`)",
        )

    summary = []
    have_any = False
    for adapter in all_adapters():
        home = getattr(adapter, "home", None)
        if home is None:
            continue
        home = Path(home).expanduser()
        present = home.exists()
        if present:
            have_any = True
            try:
                files = list(adapter.discover())
                summary.append({
                    "name": adapter.name,
                    "label": getattr(adapter, "label", adapter.name),
                    "home": str(home),
                    "present": True,
                    "session_files": len(files),
                })
            except Exception as e:
                summary.append({
                    "name": adapter.name,
                    "home": str(home),
                    "present": True,
                    "error": f"discover() raised: {e}",
                })
        else:
            summary.append({
                "name": adapter.name,
                "label": getattr(adapter, "label", adapter.name),
                "home": str(home),
                "present": False,
            })

    if not summary:
        return CheckResult(
            "source_adapters", "adapters", "fail",
            "adapter registry returned nothing",
            remedy="check `throughline.adapters.registry.BUILTIN_ADAPTERS`",
        )

    if not have_any:
        return CheckResult(
            "source_adapters", "adapters", "warn",
            "no adapter home directories exist on this machine",
            remedy="use one of: Claude Code, Codex, Hermes, Continue, Cline, Windsurf — once any of them has a session, throughline can ingest it",
            details={"adapters": summary},
        )

    present_names = [s["name"] for s in summary if s.get("present")]
    return CheckResult(
        "source_adapters", "adapters", "pass",
        f"{len(present_names)}/{len(summary)} adapter homes present: {', '.join(present_names)}",
        details={"adapters": summary},
    )


# --- 4. Embeddings backend --------------------------------------------------


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@_check("embeddings_backend", "embeddings")
def check_embeddings_backend() -> CheckResult:
    # Heuristic preference order: env vars first, then ollama default port.
    if os.environ.get("OPENAI_API_KEY"):
        return CheckResult(
            "embeddings_backend", "embeddings", "pass",
            "OpenAI API key present (1536-dim embeddings)",
            details={"backend": "openai"},
        )
    ollama_host = os.environ.get("OLLAMA_HOST", "localhost")
    ollama_port = int(os.environ.get("OLLAMA_PORT", "11434"))
    if _port_open(ollama_host, ollama_port):
        return CheckResult(
            "embeddings_backend", "embeddings", "pass",
            f"Ollama reachable at {ollama_host}:{ollama_port} (768-dim, e.g. nomic-embed-text)",
            details={"backend": "ollama", "host": ollama_host, "port": ollama_port},
        )
    return CheckResult(
        "embeddings_backend", "embeddings", "warn",
        "no embeddings backend reachable",
        remedy=(
            "either export OPENAI_API_KEY=…, or run a local Ollama "
            "(`brew install ollama && ollama serve && ollama pull nomic-embed-text`). "
            "Throughline still works with keyword-only search if you skip this."
        ),
    )


# --- 5. Scheduled jobs ------------------------------------------------------


@_check("answer_backend", "embeddings")
def check_answer_backend() -> CheckResult:
    """Which model answers questions, and whether it runs on this machine.

    Worth its own line because the answer decides where the corpus goes: a
    local model keeps every excerpt here, a hosted one does not. That should be
    visible without reading the source.
    """
    from throughline import llm

    info = llm.backend_info()
    if not info.available:
        return CheckResult(
            "answer_backend", "embeddings", "warn",
            f"no model available for `throughline ask` — {info.detail}",
            remedy="ollama pull llama3.1:8b — or set THROUGHLINE_ANSWER_BASE_URL / OPENAI_API_KEY",
            details={"available": False, "detail": info.detail},
        )
    where = "runs locally" if info.local else "sends excerpts off this machine"
    return CheckResult(
        "answer_backend", "embeddings", "pass",
        f"{info.backend}/{info.model} — {where}",
        details={"backend": info.backend, "model": info.model, "local": info.local,
                 "detail": info.detail},
    )


@_check("scheduled_jobs", "schedule")
def check_scheduled_jobs() -> CheckResult:
    if sys.platform == "darwin":
        launch_agents = Path("~/Library/LaunchAgents").expanduser()
        plists = []
        if launch_agents.exists():
            plists = sorted(p.name for p in launch_agents.glob("com.kupermann.throughline.*.plist"))
        if not plists:
            return CheckResult(
                "scheduled_jobs", "schedule", "warn",
                "no Throughline launchd jobs found in ~/Library/LaunchAgents",
                remedy="run `throughline install-hooks` (or follow docs/INSTALLATION.md) to install hourly ingest / daily extract",
            )
        return CheckResult(
            "scheduled_jobs", "schedule", "pass",
            f"{len(plists)} launchd plist(s) installed",
            details={"plists": plists},
        )
    if sys.platform.startswith("linux"):
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return CheckResult(
                "scheduled_jobs", "schedule", "warn",
                "systemctl not found; cannot check timers",
            )
        # We don't actually run systemctl from doctor (avoid sudo prompts);
        # just check whether the unit files have been copied into the
        # user systemd directory.
        user_sysd = Path("~/.config/systemd/user").expanduser()
        timers = []
        if user_sysd.exists():
            timers = sorted(p.name for p in user_sysd.glob("throughline-*.timer"))
        if not timers:
            return CheckResult(
                "scheduled_jobs", "schedule", "warn",
                "no Throughline systemd timers installed",
                remedy="copy `systemd/throughline-*.timer` and `*.service` into ~/.config/systemd/user/ and run `systemctl --user enable --now throughline-ingest.timer`",
            )
        return CheckResult(
            "scheduled_jobs", "schedule", "pass",
            f"{len(timers)} systemd timer(s) installed",
            details={"timers": timers},
        )
    return CheckResult(
        "scheduled_jobs", "schedule", "warn",
        f"automated scheduling not supported on {sys.platform}; run ingest manually",
    )


# --- 6. Archive integrity ---------------------------------------------------
#
# These exist because of a measurement, not a hunch: of 3,630 Claude Code
# transcripts this tool has ingested, 91% no longer exist on disk — the source
# CLI rotated them away. For those the database is not a convenient index over
# files that still exist somewhere. It is the only surviving copy, and nothing
# else can ever be used to reconstruct it. A store in that position has to be
# checkable, or "we still have your history" is a claim nobody can test.


@_check("archive_consistency", "archive")
def check_archive_consistency() -> CheckResult:
    """Internal contradictions the database can detect about itself.

    Three cheap questions with no legitimate non-zero answer:

    - messages whose conversation no longer exists (a broken parent link),
    - active memory chunks pointing at a conversation that is gone (memory
      whose provenance can no longer be shown),
    - ``conversations.message_count`` disagreeing with the rows actually
      present. That column is denormalised and it is not cosmetic: the
      extraction and entity queues both filter on ``message_count >= N``, so a
      wrong value silently changes which conversations are ever processed.
    """
    from throughline.status import _connect

    conn = _connect()
    if conn is None:
        return CheckResult("archive_consistency", "archive", "warn", "skipped (Postgres not reachable)")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  (SELECT count(*) FROM messages m
                     WHERE NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = m.conversation_id)),
                  (SELECT count(*) FROM memory_chunks mc
                     WHERE mc.source_type = 'conversation' AND mc.source_id IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = mc.source_id)),
                  (SELECT count(*) FROM (
                      SELECT c.id FROM conversations c
                      LEFT JOIN messages m ON m.conversation_id = c.id
                      GROUP BY c.id, c.message_count
                      HAVING c.message_count IS DISTINCT FROM count(m.id)) t)
                """
            )
            orphan_messages, dangling_chunks, count_drift = (int(v) for v in cur.fetchone())
    finally:
        conn.close()

    details = {
        "orphan_messages": orphan_messages,
        "dangling_memory_chunks": dangling_chunks,
        "message_count_drift": count_drift,
    }
    problems = [f"{v} {k.replace('_', ' ')}" for k, v in details.items() if v]
    if not problems:
        return CheckResult(
            "archive_consistency", "archive", "pass",
            "no orphaned rows, no dangling memory, message counts agree",
            details=details,
        )
    # One remedy per fault actually present. A hint about a problem the reader
    # does not have is noise, and noise is how a warning stops being read.
    remedies = []
    if count_drift:
        remedies.append(
            "recompute the counts: UPDATE conversations c SET message_count = "
            "(SELECT count(*) FROM messages m WHERE m.conversation_id = c.id) "
            "WHERE c.message_count IS DISTINCT FROM (SELECT count(*) FROM messages m "
            "WHERE m.conversation_id = c.id) — re-ingesting will not fix these, "
            "since most of their source files no longer exist on disk"
        )
    if dangling_chunks:
        remedies.append(
            "dangling chunks keep their content and lose only their link to the "
            "conversation they came from; deleting them would destroy memory, so "
            "leave them unless you know the conversation is gone for good"
        )
    if orphan_messages:
        remedies.append(
            "orphaned messages should be impossible — messages.conversation_id "
            "carries a foreign key. Investigate before deleting anything"
        )

    # Warn, not fail: every one of these is a bookkeeping fault, not lost
    # content — the messages themselves are intact. Failing would put doctor
    # into a permanent red state over something that never blocks a read.
    return CheckResult(
        "archive_consistency", "archive", "warn",
        "; ".join(problems),
        remedy="; ".join(remedies) or None,
        details=details,
    )


@_check("archive_backup", "archive")
def check_archive_backup() -> CheckResult:
    """Is there a recent, non-empty backup of the only copy?

    Redundancy is the first property an archive owes its owner, and it is the
    one that fails silently: a scheduled job that stopped working leaves
    exactly the same evidence as one that never existed.
    """
    # Same resolution order as scripts/backup.sh, so the check looks where the
    # job writes. If those two ever disagree, this reports "no backups" beside
    # a directory full of them.
    override = os.environ.get("CLAUDE_MEMORY_BACKUP_DIR")
    if override:
        backup_dir = Path(override)
    else:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        backup_dir = data_home / "claude-memory" / "backups"

    if not backup_dir.is_dir():
        return CheckResult(
            "archive_backup", "archive", "warn",
            f"no backup directory at {backup_dir}",
            remedy="bash scripts/install_backup_agent.sh",
            details={"backup_dir": str(backup_dir)},
        )

    dumps = sorted(backup_dir.glob("*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    dumps = [p for p in dumps if p.stat().st_size > 0]
    if not dumps:
        return CheckResult(
            "archive_backup", "archive", "warn",
            f"backup directory holds no usable dump ({backup_dir})",
            remedy="bash scripts/backup.sh",
            details={"backup_dir": str(backup_dir)},
        )

    newest = dumps[0]
    age_h = (time.time() - newest.stat().st_mtime) / 3600.0
    size_mb = newest.stat().st_size / 1024 / 1024
    details = {
        "newest": newest.name,
        "age_hours": round(age_h, 1),
        "size_mb": round(size_mb, 1),
        "count": len(dumps),
    }
    # 48h, not 24: the schedule is daily, so a 24h threshold would report a
    # warning every day in the hours before the run rather than only when a
    # run has actually been missed.
    if age_h > 48:
        return CheckResult(
            "archive_backup", "archive", "warn",
            f"newest backup is {age_h / 24:.1f} days old ({newest.name})",
            remedy="bash scripts/backup.sh — and check the agent: launchctl list | grep claude-memory",
            details=details,
        )
    return CheckResult(
        "archive_backup", "archive", "pass",
        f"{len(dumps)} backup(s), newest {age_h:.1f}h old, {size_mb:.0f} MB",
        details=details,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


_ALL_CHECKS: list[Callable[[], CheckResult]] = [
    check_python_version,
    check_required_packages,
    check_optional_packages,
    check_postgres_reachable,
    check_pgvector,
    check_schema_present,
    check_source_adapters,
    check_embeddings_backend,
    check_answer_backend,
    check_scheduled_jobs,
    check_archive_consistency,
    check_archive_backup,
]


def run_doctor(categories: Iterable[str] | None = None) -> DoctorReport:
    """Run every doctor check and return a structured report.

    ``categories``, if provided, filters to checks whose ``category`` is in
    the set. Default: run all.
    """
    wanted = set(categories) if categories else None
    report = DoctorReport()
    for check in _ALL_CHECKS:
        result = check()
        if wanted is None or result.category in wanted:
            report.checks.append(result)
    return report


_STATUS_GLYPHS = {"pass": "✓", "warn": "⚠", "fail": "✗"}
_STATUS_COLORS = {"pass": "\033[32m", "warn": "\033[33m", "fail": "\033[31m"}
_RESET = "\033[0m"


def format_human(report: DoctorReport, *, color: bool = True) -> str:
    """Render the report as a human-readable text block, grouped by category."""
    lines: list[str] = []
    by_cat: dict[str, list[CheckResult]] = {}
    for c in report.checks:
        by_cat.setdefault(c.category, []).append(c)

    # Known categories first, in reading order; anything else after, rather
    # than dropped. A fixed list silently swallowed the whole `archive`
    # category once — its checks ran, counted toward the summary, and never
    # printed, so the totals disagreed with what was on screen and the only
    # way to see them was `--json`.
    order = ["python", "postgres", "adapters", "embeddings", "schedule"]
    order += [c for c in by_cat if c not in order]
    for cat in order:
        if cat not in by_cat:
            continue
        lines.append(f"── {cat} ──")
        for c in by_cat[cat]:
            glyph = _STATUS_GLYPHS.get(c.status, "?")
            if color and sys.stdout.isatty():
                glyph = f"{_STATUS_COLORS.get(c.status, '')}{glyph}{_RESET}"
            lines.append(f"  {glyph}  {c.name:<22} {c.message}")
            if c.remedy and c.status != "pass":
                lines.append(f"        → {c.remedy}")
        lines.append("")

    s = report.passes, report.warns, report.fails
    lines.append(f"Summary: {s[0]} pass · {s[1]} warn · {s[2]} fail")
    return "\n".join(lines)
