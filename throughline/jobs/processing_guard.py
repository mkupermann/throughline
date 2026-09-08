"""Terminate the command tree if its owning worker disappears, including SIGKILL."""

import os
import signal
import subprocess
import sys
import time

import psutil


def terminate_tree(proc, force=False):
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        try:
            processes = psutil.Process(proc.pid).children(recursive=True) + [psutil.Process(proc.pid)]
            for child in processes:
                try:
                    child.kill() if force else child.terminate()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass


def guard(args, owner_pid, owner_started, timeout):
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def owner_alive():
        try:
            owner = psutil.Process(owner_pid)
            return owner.create_time() == owner_started and owner.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    if not owner_alive():
        return 1
    proc = subprocess.Popen(args, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if stopped or not owner_alive() or time.monotonic() >= deadline:
                terminate_tree(proc)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                # Descendants may survive a quickly exiting group leader.
                terminate_tree(proc, force=True)
                return proc.wait(timeout=3) or -15
            time.sleep(0.2)
        return proc.returncode
    finally:
        terminate_tree(proc, force=True)


def main():
    from throughline.api.jobs import JOBS

    name, parent_pid, parent_started = sys.argv[1:]
    spec = JOBS[name]
    return guard(spec.args, int(parent_pid), float(parent_started), 86400 if name == "process-all" else 3600)


if __name__ == "__main__":
    raise SystemExit(main())
