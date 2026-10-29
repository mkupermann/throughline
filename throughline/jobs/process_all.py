"""One complete processing pass, in dependency order, with no UI batch caps."""

import json
import os
import subprocess

from throughline.api.jobs import JOBS, check_requirement

STEPS = (
    "ingest",
    "scan-skills",
    "scan-prompts",
    "project-names",
    "titles",
    "extract",
    "entities",
    "reflect",
    "embed",
    "audit-extraction",
    "doctor",
)
FULL_LIMIT = str(2_147_483_647)


def command(name):
    args = list(JOBS[name].args)
    if name in ("extract", "entities"):
        args += ["--limit", FULL_LIMIT]
    if name == "reflect":
        args += ["--limit", FULL_LIMIT, "--max-pairs", FULL_LIMIT, "--dry-run"]
    return args


def status(name, state, detail=""):
    run_id = os.environ.get("THROUGHLINE_PROCESSING_RUN_ID")
    if run_id:
        from psycopg2.extras import Json

        from throughline.api.durable_jobs import database
        from throughline.queries._exec import one

        # Persist trusted control state directly. Child stdout is never a checkpoint protocol.
        with database() as conn:
            result = one(
                conn,
                """UPDATE processing_runs SET stages=jsonb_set(stages,%s,%s,true)
                WHERE id=%s AND name='process-all' AND state='running' RETURNING id""",
                ([name], Json({"name": name, "state": state, "detail": detail}), run_id),
            )
            if not result:
                raise RuntimeError("Processing run no longer owns an active checkpoint.")
    print("::stage " + json.dumps({"name": name, "state": state, "detail": detail}), flush=True)


def main():
    failures = []
    completed = set(json.loads(os.environ.get("THROUGHLINE_RESUME_STAGES", "[]")))
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "THROUGHLINE_TITLE_LIMIT": "0"}
    print(
        "Complete processing pass. Each pending item is attempted once; errors and valid empty extractions remain visible.",
        flush=True,
    )
    for index, name in enumerate(STEPS, 1):
        spec = JOBS[name]
        if name in completed:
            status(name, "finished", "Retained from the previous worker attempt")
            print(f"RETAINED: {spec.title}", flush=True)
            continue
        status(name, "running")
        print(f"\n[{index}/{len(STEPS)}] {spec.title}", flush=True)
        reason = check_requirement(spec.requires)
        if reason:
            failures.append(name)
            status(name, "blocked", reason)
            print(f"BLOCKED: {reason}", flush=True)
            continue
        try:
            code = subprocess.run(command(name), env=env, check=False).returncode
        except OSError:
            code = 1
        if code:
            failures.append(name)
            status(name, "failed", f"exit {code}")
            print(f"FAILED: {spec.title} (exit {code}); continuing other steps.", flush=True)
        else:
            status(name, "finished")
            print(f"FINISHED: {spec.title}", flush=True)
    if failures:
        print("\nIncomplete pass. Failed or blocked steps: " + ", ".join(failures), flush=True)
        raise SystemExit(1)
    print(
        "\nProcessing pass finished. Review remaining pending items and unconfirmed knowledge in the refreshed status.",
        flush=True,
    )


if __name__ == "__main__":
    main()
