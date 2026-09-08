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

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal


class JobUnavailable(RuntimeError):
    """A job's environment prerequisites are not met."""


MAX_LINES = 500
#: Hard ceiling so a hung job cannot occupy a slot forever.
MAX_RUNTIME_SECONDS = 60 * 60


#: What a job needs from the environment before it can possibly succeed.
#: Checked before the Run button is offered, so a job that cannot work here
#: says why instead of failing after the user commits to it.
#:
#: "model" is any generation backend `throughline.llm` can find — Ollama, or
#: an OpenAI-compatible server. There used to be a separate "claude"
#: requirement for jobs that shelled out to that CLI; in the container it meant
#: no extraction, no titles and no reflection at all, because the CLI carries
#: host credentials and is deliberately not in the image. Those jobs go through
#: the shared backend now, so the requirement is gone with them.
Requirement = Literal[
    "model", "embedding", "model:titles", "model:project_names", "model:extraction", "model:reflection"
]


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
    if req.startswith("model"):
        from throughline import llm

        info = llm.backend_info(purpose=req.split(":", 1)[1]) if ":" in req else llm.backend_info()
        return None if info.available else info.detail
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


def _job_module(module: str, *args: str) -> list[str]:
    """Run one packaged job module with this interpreter."""
    return [sys.executable, "-m", module, *args]


JOBS: dict[str, JobSpec] = {
    "process-all": JobSpec(
        "process-all",
        "Process everything",
        "Import, scan catalogues, name projects and conversations, extract knowledge and entities, reflect, embed, audit and diagnose — one complete pass.",
        _job_module("throughline.jobs.process_all"),
    ),
    "project-names": JobSpec(
        "project-names",
        "Name projects",
        "Suggest readable names from source conversations, preserving your own names.",
        _job_module("throughline.jobs.name_projects"),
        requires="model:project_names",
    ),
    "entities": JobSpec(
        "entities",
        "Extract entities",
        "Extract people, technologies and other entities from conversations.",
        _job_module("throughline.jobs.extract_entities"),
        requires="model:extraction",
    ),
    "ingest": JobSpec(
        "ingest",
        "Ingest sessions",
        "Import new sessions from every configured AI coding tool.",
        _cli("ingest", "--all"),
    ),
    "scan-skills": JobSpec(
        "scan-skills",
        "Scan skills",
        "Re-scan SKILL.md files and refresh the skill catalogue.",
        _cli("scan-skills"),
    ),
    "scan-prompts": JobSpec(
        "scan-prompts",
        "Scan prompts",
        "Re-scan prompt files.",
        _cli("scan-prompts"),
    ),
    "extract": JobSpec(
        "extract",
        "Extract memory",
        "Run the LLM extraction pass over conversations with no memory yet.",
        _cli("extract-memory"),
        requires="model:extraction",
    ),
    "embed": JobSpec(
        "embed",
        "Generate embeddings",
        "Embed chunks that semantic search cannot currently reach.",
        _cli("embed", "--backend", "auto"),
        requires="embedding",
    ),
    "titles": JobSpec(
        "titles",
        "Generate titles",
        "Summarise conversations that have no title.",
        _cli("generate-titles"),
        requires="model:titles",
    ),
    "reflect": JobSpec(
        "reflect",
        "Run reflection",
        "Deduplicate, find contradictions, mark stale memory.",
        _cli("reflect"),
        requires="model:reflection",
    ),
    "audit-extraction": JobSpec(
        "audit-extraction",
        "Run drift audit",
        "Check a sample of extracted memory against its source conversations.",
        _job_module("throughline.jobs.audit_extraction", "--json"),
    ),
    "export-markdown": JobSpec(
        "export-markdown",
        "Export as Markdown",
        "Write the corpus out as a Markdown vault, one folder per project.",
        # No destination here. It arrives in the environment, so a request
        # body still never becomes part of a command line.
        _cli("export-markdown"),
    ),
    "doctor": JobSpec(
        "doctor",
        "Diagnostics",
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
    stages: dict[str, dict] = field(default_factory=dict)
    _cancel: threading.Event = field(default_factory=threading.Event)

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
                "stages": dict(self.stages),
            }

    def append(self, line: str) -> None:
        with self._lock:
            if len(self.lines) == self.lines.maxlen:
                self._dropped += 1
            self.lines.append(line)
            if line.startswith("::stage "):
                try:
                    stage = json.loads(line[len("::stage ") :])
                    self.stages[stage["name"]] = stage
                except (ValueError, KeyError, TypeError):
                    pass
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
        self._current: dict[str, str] = {}  # job name -> job id
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
            all_id = self._current.get("process-all")
            if name != "process-all" and all_id and self._by_id[all_id].running:
                raise JobUnavailable(
                    "The complete processing pass is already running. Open its progress or stop it first."
                )
            predecessors = [j for j in self._by_id.values() if j.running] if name == "process-all" else []
            running_id = self._current.get(name)
            if running_id:
                existing = self._by_id.get(running_id)
                if existing and existing.running:
                    return existing

            job = Job(id=uuid.uuid4().hex[:12], name=name, started_at=time.time())
            self._by_id[job.id] = job
            self._current[name] = job.id
            self._history.appendleft(job.id)

        threading.Thread(target=self._launch, args=(job, spec, extra_env, predecessors), daemon=True).start()
        return job

    def _launch(self, job, spec, extra_env, predecessors):
        if predecessors:
            job.append("Waiting for previously started jobs to finish before the complete pass.")
        while any(other.running for other in predecessors):
            if job._cancel.wait(0.25):
                job.finish(-15, "Stopped while waiting")
                return
        if job._cancel.is_set():
            job.finish(-15, "Stopped before launch")
            return
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
                start_new_session=True,
            )
        except Exception as exc:
            job.append(f"failed to start: {exc}")
            job.finish(None, error=str(exc))
            return job

        job._proc = proc
        if job._cancel.is_set():
            self.stop(job.id)
        self._pump(job, proc)

    def _pump(self, job: Job, proc: subprocess.Popen) -> None:
        max_runtime = 24 * 60 * 60 if job.name == "process-all" else MAX_RUNTIME_SECONDS

        def expire():
            if proc.poll() is None:
                job.append(f"Time limit reached ({max_runtime}s); unfinished work remains pending.")
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        timer = threading.Timer(max_runtime, expire)
        timer.daemon = True
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                job.append(line.rstrip("\n"))
            proc.wait(timeout=30)
            job.finish(proc.returncode)
        except Exception as exc:
            job.finish(None, error=str(exc))
        finally:
            timer.cancel()
            # Coverage caches the filesystem side for up to CACHE_TTL_SECONDS
            # (throughline/queries/providers.py) — without this an ingest that
            # just succeeded keeps reporting pre-ingest counts, which reads as
            # "the ingest did nothing". Unconditional: even a failed or
            # partial ingest may have changed what's on disk or in the log.
            if job.name.startswith("ingest") or job.name == "process-all":
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
        job = self._by_id.get(jid) if jid else None
        return job if job and job.running else None

    def stop(self, job_id: str) -> bool:
        job = self._by_id.get(job_id)
        if job is None or not job.running:
            return False
        job._cancel.set()
        if job._proc is not None:
            try:
                os.killpg(job._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        def force_stop():
            if job._proc is not None and job._proc.poll() is None:
                try:
                    os.killpg(job._proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        timer = threading.Timer(5, force_stop)
        timer.daemon = True
        timer.start()
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
            start = max(0, sent - snap["dropped_lines"])
            new = snap["lines"][start:]
            for line in new:
                yield _sse("line", line)
            sent = snap["dropped_lines"] + len(snap["lines"])

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


from .durable_jobs import DurableJobRunner  # noqa: E402

runner = DurableJobRunner()
