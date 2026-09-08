import sys
import time

import pytest

from throughline.api import jobs


def wait(job):
    end = time.monotonic() + 5
    while job.running and time.monotonic() < end:
        time.sleep(0.02)
    assert not job.running


def test_all_waits_for_existing_work_and_is_exclusive(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "JOBS",
        {
            "old": jobs.JobSpec("old", "Old", "", [sys.executable, "-c", "import time;time.sleep(.3)"]),
            "process-all": jobs.JobSpec("process-all", "All", "", [sys.executable, "-c", 'print("complete")']),
        },
    )
    runner = jobs.JobRunner()
    first = runner.start("old")
    whole = runner.start("process-all")
    assert runner.start("process-all").id == whole.id
    with pytest.raises(jobs.JobUnavailable):
        runner.start("old")
    wait(whole)
    assert not first.running and whole.returncode == 0
    assert "complete" in whole.snapshot()["lines"]
    assert runner.current("process-all") is None


def test_stop_queued_full_pass_does_not_start_it(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "JOBS",
        {
            "old": jobs.JobSpec("old", "Old", "", [sys.executable, "-c", "import time;time.sleep(.4)"]),
            "process-all": jobs.JobSpec("process-all", "All", "", [sys.executable, "-c", 'print("must not run")']),
        },
    )
    runner = jobs.JobRunner()
    old = runner.start("old")
    whole = runner.start("process-all")
    assert runner.stop(whole.id)
    wait(whole)
    wait(old)
    assert "must not run" not in whole.snapshot()["lines"]
    assert whole.returncode != 0


def test_progress_survives_bounded_log_rollover():
    job = jobs.Job("id", "process-all", time.time())
    job.append('::stage {"name":"titles","state":"finished"}')
    for i in range(jobs.MAX_LINES + 10):
        job.append(str(i))
    assert job.snapshot()["stages"]["titles"]["state"] == "finished"
