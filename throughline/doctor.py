"""Throughline doctor — diagnose install / runtime issues.

``throughline doctor`` runs a structured battery of checks across five
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

Each check returns a ``CheckResult`` dataclass. The CLI module formats
them; the JSON mode emits the raw list so other tools (CI, health
endpoints) can consume it.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str                         # short stable identifier ("python_version")
    category: str                     # one of the five categories above
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
        db = os.environ.get("PGDATABASE", "claude_memory")
        return CheckResult(
            "postgres_reachable", "postgres", "fail",
            f"cannot connect to Postgres at {host}:{port}/{db}",
            remedy=(
                "start Postgres (e.g. `pg_ctl start` / `brew services start postgresql@16`) "
                "or `docker compose up -d`; check PGHOST/PGPORT/PGDATABASE env vars"
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
    check_scheduled_jobs,
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

    order = ["python", "postgres", "adapters", "embeddings", "schedule"]
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
