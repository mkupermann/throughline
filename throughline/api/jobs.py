"""Background job runner for the pipeline commands.

The Streamlit app ran these with a blocking ``subprocess.run`` inside a script
rerun: the page froze for the duration and you learned nothing until it ended.
Here each job is a detached subprocess whose output is streamed to any number
of watchers over SSE, so the UI stays interactive and shows progress as it
happens.

Design constraints that shaped this:

* **One run per job at a time.** Two concurrent ``ingest`` processes would race
  on the same tables to no benefit, so starting a job that is already running
  returns the running job rather than spawning a second.
* **Output is bounded.** A long ingest can emit tens of thousands of lines;
  only the last ``MAX_LINES`` are retained, because the purpose is progress and
  a final verdict, not a full log store.
* **Jobs are a fixed registry, never a caller-supplied command.** The HTTP
  layer passes a job *name*; there is no path by which a request body becomes
  argv.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Iterator, Literal

class JobUnavailable(RuntimeError):
    """A job's environment prerequisites are not met."""


MAX_LINES = 500
#: Hard ceiling so a hung job cannot occupy a slot forever.
MAX_RUNTIME_SECONDS = 60 * 60


#: What a job needs from the environment before it can possibly succeed.
#: Checked before the Run button is offered, so a job that cannot work here
#: says why instead of failing after the user commits to it.
#:
#: "model" is any answering backend `throughline.llm` can find — Ollama, an
#: OpenAI-compatible server, the Claude CLI. It used to be "claude" for these
#: jobs, which in the container meant no extraction, no titles and no
#: reflection at all: the CLI carries host credentials and is deliberately not
#: in the image. With a local backend they now run there.
Requirement = Literal["model", "claude", "embedding"]


@dataclass(frozen=True)
class JobSpec:
    name: str
    title: str
    description: str
    args: list[str]
    #: Shown in the UI before the user commits to running it.
    danger: str | None = None
    requires: Requirement | None = None


def check_requirement(req: Requirement | None) -> str | None:
    """Return why *req* is unmet, or None if the job can run.

    Offering a control that cannot work and reporting the failure only after
    the user commits is the same mistake as a search that silently returns
    less than it should: the system knew, and did not say.
    """
    if req is None:
        return None
    if req == "model":
        from throughline import llm

        info = llm.backend_info()
        return None if info.available else info.detail
    if req == "claude":
        from throughline.config import get_claude_bin

        if get_claude_bin():
            return None
        return (
            "The Claude CLI is not available here. This job calls `claude -p`, "
            "which needs your host login — it is deliberately not in the "
            "container image. Run this on the host: `throughline <command>`, "
            "or set CLAUDE_BIN."
        )
    if req == "embedding":
        from throughline import embedding

        info = embedding.backend_info()
        return None if info.available else info.reason
    return None


def _cli(*args: str) -> list[str]:
    """Invoke this interpreter's throughline CLI, not whatever is on PATH.

    A server running inside a venv must not shell out to a different install —
    that is how you get a job writing to the wrong database.
    """
    return [sys.executable, "-m", "throughline", *args]


JOBS: dict[str, JobSpec] = {
    "ingest": JobSpec(
        "ingest", "Ingest sessions",
        "Import new sessions from every configured AI coding tool.",
        _cli("ingest", "--all"),
    ),
    "scan-skills": JobSpec(
        "scan-skills", "Scan skills",
        "Re-scan SKILL.md files and refresh the skill catalogue.",
        _cli("scan-skills"),
    ),
    "scan-prompts": JobSpec(
        "scan-prompts", "Scan prompts",
        "Re-scan prompt files.",
        _cli("scan-prompts"),
    ),
    "extract": JobSpec(
        "extract", "Extract memory",
        "Run the LLM extraction pass over conversations with no memory yet.",
        _cli("extract-memory"),
        requires="model",
    ),
    "embed": JobSpec(
        "embed", "Generate embeddings",
        "Embed chunks that semantic search cannot currently reach.",
        _cli("embed", "--backend", "auto"),
        requires="embedding",
    ),
    "titles": JobSpec(
        "titles", "Generate titles",
        "Summarise conversations that have no title.",
        _cli("generate-titles"),
        requires="model",
    ),
    "reflect": JobSpec(
        "reflect", "Run reflection",
        "Deduplicate, find contradictions, mark stale memory.",
        _cli("reflect"),
        requires="model",
    ),
    "doctor": JobSpec(
        "doctor", "Diagnostics",
        "Check the install, database and extensions.",
        _cli("doctor"),
    ),
}


def _per_provider_jobs() -> dict[str, JobSpec]:
    """One ingest job per adapter.

    Targeted rather than `--all` because the Overview item that surfaces an
    un-ingested source should lead to importing exactly that source.
    """
    from throughline import providers as P

    return {
        f"ingest_{p.name}": JobSpec(
            f"ingest_{p.name}",
            f"Ingest {p.label}",
            f"Import new {p.label} sessions.",
            _cli("ingest", "--source", p.name),
        )
        for p in P.PROVIDERS
    }


JOBS.update(_per_provider_jobs())


@dataclass
class Job:
    id: str
    name: str
    started_at: float
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    returncode: int | None = None
    finished_at: float | None = None
    error: str | None = None
    _proc: subprocess.Popen | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _event: threading.Event = field(default_factory=threading.Event)
    _dropped: int = 0

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "name": self.name,
                "running": self.running,
                "returncode": self.returncode,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_s": round((self.finished_at or time.time()) - self.started_at, 1),
                "lines": list(self.lines),
                "dropped_lines": self._dropped,
                "error": self.error,
            }

    def append(self, line: str) -> None:
        with self._lock:
            if len(self.lines) == self.lines.maxlen:
                self._dropped += 1
            self.lines.append(line)
        self._event.set()

    def finish(self, returncode: int | None, error: str | None = None) -> None:
        with self._lock:
            self.returncode = returncode
            self.error = error
            self.finished_at = time.time()
        self._event.set()

    def wait_for_change(self, timeout: float) -> bool:
        fired = self._event.wait(timeout)
        self._event.clear()
        return fired


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, Job] = {}
        self._current: dict[str, str] = {}   # job name -> job id
        self._history: deque[str] = deque(maxlen=50)

    def start(self, name: str, extra_env: dict[str, str] | None = None) -> Job:
        spec = JOBS.get(name)
        if spec is None:
            raise KeyError(name)

        # Belt and braces: the UI disables these, but a direct POST must not
        # spawn a process that is guaranteed to fail.
        unmet = check_requirement(spec.requires)
        if unmet:
            raise JobUnavailable(unmet)

        with self._lock:
            running_id = self._current.get(name)
            if running_id:
                existing = self._by_id.get(running_id)
                if existing and existing.running:
                    return existing

            job = Job(id=uuid.uuid4().hex[:12], name=name, started_at=time.time())
            self._by_id[job.id] = job
            self._current[name] = job.id
            self._history.appendleft(job.id)

        env = {**os.environ, "PYTHONUNBUFFERED": "1", **(extra_env or {})}
        job.append(f"$ {shlex.join(spec.args)}")
        try:
            proc = subprocess.Popen(
                spec.args,
                cwd=None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            job.append(f"failed to start: {exc}")
            job.finish(None, error=str(exc))
            return job

        job._proc = proc
        threading.Thread(target=self._pump, args=(job, proc), daemon=True).start()
        return job

    def _pump(self, job: Job, proc: subprocess.Popen) -> None:
        deadline = time.time() + MAX_RUNTIME_SECONDS
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                job.append(line.rstrip("\n"))
                if time.time() > deadline:
                    proc.kill()
                    job.append(f"killed after {MAX_RUNTIME_SECONDS}s")
                    break
            proc.wait(timeout=30)
            job.finish(proc.returncode)
        except Exception as exc:
            job.finish(None, error=str(exc))
        finally:
            # Coverage caches the filesystem side for up to CACHE_TTL_SECONDS
            # (throughline/queries/providers.py) — without this an ingest that
            # just succeeded keeps reporting pre-ingest counts, which reads as
            # "the ingest did nothing". Unconditional: even a failed or
            # partial ingest may have changed what's on disk or in the log.
            if job.name.startswith("ingest"):
                from throughline.queries import providers as PQ

                PQ.invalidate_scan_cache()
            with self._lock:
                if self._current.get(job.name) == job.id:
                    self._current.pop(job.name, None)

    def get(self, job_id: str) -> Job | None:
        return self._by_id.get(job_id)

    def current(self, name: str) -> Job | None:
        with self._lock:
            jid = self._current.get(name)
        return self._by_id.get(jid) if jid else None

    def stop(self, job_id: str) -> bool:
        job = self._by_id.get(job_id)
        if job is None or not job.running or job._proc is None:
            return False
        job._proc.terminate()
        job.append("stop requested")
        return True

    def history(self) -> list[dict]:
        with self._lock:
            ids = list(self._history)
        out = []
        for jid in ids:
            job = self._by_id.get(jid)
            if job:
                snap = job.snapshot()
                snap.pop("lines", None)
                out.append(snap)
        return out

    def stream(self, job: Job, poll: float = 0.5) -> Iterator[str]:
        """Server-sent events for one job: replay, then live, then a final event."""
        sent = 0
        while True:
            snap = job.snapshot()
            new = snap["lines"][sent:]
            for line in new:
                yield _sse("line", line)
            sent = len(snap["lines"])

            if not snap["running"]:
                yield _sse(
                    "done",
                    f"exit={snap['returncode']} duration={snap['duration_s']}s"
                    + (f" error={snap['error']}" if snap["error"] else ""),
                )
                return

            if not job.wait_for_change(poll):
                # Comment frame keeps proxies and the browser from timing the
                # connection out during a quiet stretch of a long job.
                yield ": keepalive\n\n"


def _sse(event: str, data: str) -> str:
    # Every newline must be its own `data:` line or the frame is malformed.
    body = "\n".join(f"data: {part}" for part in str(data).split("\n"))
    return f"event: {event}\n{body}\n\n"


runner = JobRunner()
