"""A SIGKILLed worker must not leave its model/job process tree running."""

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL crash exercise uses POSIX signals")
def test_guard_stops_command_after_owner_is_killed(tmp_path):
    from throughline.jobs.processing_guard import __file__ as guard_file

    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    output = tmp_path / "child.pid"
    child_code = (
        "import os,time,pathlib; pathlib.Path(" + repr(str(output)) + ").write_text(str(os.getpid())); time.sleep(120)"
    )
    code = (
        "from throughline.jobs.processing_guard import guard; import sys; sys.exit(guard("
        + repr([sys.executable, "-c", child_code])
        + ","
        + str(owner.pid)
        + ","
        + str(psutil.Process(owner.pid).create_time())
        + ",120))"
    )
    guard = subprocess.Popen([sys.executable, "-c", code], cwd=Path(guard_file).parents[2])
    child = None
    try:
        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert output.exists()
        child = psutil.Process(int(output.read_text()))
        owner.kill()
        owner.wait(timeout=2)
        guard.wait(timeout=7)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        for proc in (owner, guard):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        if child:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
