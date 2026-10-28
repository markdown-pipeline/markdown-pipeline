"""MD-179 AC4: for each lock the app takes, a second PROCESS gets "busy" (never a second holder), and the lock frees when the holder dies.

Each test starts a real child process that takes the lock through the app's own code, then checks from this process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from mdpipeline import background, usage
from mdpipeline.locking import BusyError, brain_lock, is_locked
from mdpipeline.schedulestore import SchedulerStore


def _child(code: str, *args: str) -> subprocess.Popen:
    """Run ``code`` in a new interpreter with this test's environment (data dir, no background start) and wait until it prints ``held``."""
    env = {**os.environ, "MDPIPELINE_NO_BACKGROUND_START": "1"}
    child = subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), *args], stdout=subprocess.PIPE, text=True, env=env)
    assert child.stdout.readline().strip() == "held", "the child did not take the lock"
    return child


def _wait_until(check, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds              # Windows lets go of a dead process's lock a moment after it ends
    while not check():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


@pytest.fixture
def child():
    started: list[subprocess.Popen] = []
    yield lambda code, *args: started.append(_child(code, *args)) or started[-1]
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_brain_lock_a_second_process_is_busy_and_death_frees_it(brain, child) -> None:
    holder = child(
        """
        import sys, time
        from pathlib import Path
        from mdpipeline.brain import Brain
        from mdpipeline.locking import brain_lock
        with brain_lock(Brain(Path(sys.argv[1]))):
            print("held", flush=True)
            time.sleep(60)
        """,
        str(brain.root),
    )
    assert is_locked(brain) is True
    with pytest.raises(BusyError):
        with brain_lock(brain):
            pass
    holder.kill()
    holder.wait()
    assert _wait_until(lambda: not is_locked(brain))


def test_background_lock_a_second_process_is_not_a_second_background(child) -> None:
    holder = child(
        """
        import time
        from mdpipeline import background
        background.run(sleep=lambda s: (print("held", flush=True), time.sleep(60)), interval=60)
        """
    )
    assert background.is_running() is True
    assert background.run(max_ticks=1, sleep=lambda s: None) is False
    holder.kill()
    holder.wait()
    assert _wait_until(lambda: not background.is_running())


def test_scheduler_store_lock_a_second_process_waits_for_the_holder(child) -> None:
    holder = child(
        """
        import time
        from mdpipeline.schedulestore import SchedulerStore
        with SchedulerStore().update():
            print("held", flush=True)
            time.sleep(60)
        """
    )
    finished = threading.Event()

    def update_here() -> None:
        with SchedulerStore().update() as data:
            data["runtime"]["touched"] = True
        finished.set()

    worker = threading.Thread(target=update_here, daemon=True)
    worker.start()
    assert finished.wait(0.7) is False                  # still waiting: the other process holds the file lock
    holder.kill()
    holder.wait()
    assert finished.wait(10) is True                    # and goes ahead once the holder is gone
    worker.join(5)


def test_usage_queue_lock_a_second_process_is_refused_and_death_frees_it(child, tmp_path: Path) -> None:
    path = tmp_path / "usage.lock"
    holder = child(
        """
        import sys, time
        from pathlib import Path
        from mdpipeline import usage
        with usage._file_lock(Path(sys.argv[1])) as taken:
            print("held" if taken else "busy", flush=True)
            time.sleep(60)
        """,
        str(path),
    )
    with usage._file_lock(path, blocking=False) as taken:
        assert taken is False
    holder.kill()
    holder.wait()

    def taken_now() -> bool:
        with usage._file_lock(path, blocking=False) as taken:
            return taken

    assert _wait_until(taken_now)
