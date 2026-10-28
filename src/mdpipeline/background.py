"""The background process (MD-121): a small loop that keeps every registered brain current while the computer is on, with or
without the UI open. Started at login (see ``autostart``). One instance at a time, held by a lock in the app data directory.
A tick that fails is skipped; the process only ends when asked to."""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable

from . import filelock, scheduler
from .vault import data_dir

TICK_SECONDS = 60
STARTUP_WAIT_SECONDS = 3.0          # MD-166: how long a command waits for a process it just started to take the lock
STARTUP_POLL_SECONDS = 0.2
started_this_run = False             # set by ensure_running: this command has just started the process


def _lock_path():
    return data_dir() / "background.lock"


def _stop_path():
    return data_dir() / "background.stop"


def stop_requested() -> bool:
    """MD-181: someone asked this process to end (Windows has no clean signal, so the ask is a file the loop looks at)."""
    return _stop_path().exists()


def _clear_stop_request() -> None:
    try:
        _stop_path().unlink()
    except OSError:
        pass


def _sleep_until_asked_to_stop(seconds: float) -> None:
    """The default sleep: in short slices, so a stop request is seen within a second."""
    end = time.monotonic() + seconds
    while not stop_requested() and (left := end - time.monotonic()) > 0:
        time.sleep(min(0.5, left))


def is_running() -> bool:
    """Whether a background process holds the lock right now."""
    path = _lock_path()
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if filelock.try_lock(fd):
            return False
        return True
    finally:
        os.close(fd)


def stop() -> bool:
    """Ask the running background process (the holder of the lock) to end. Returns whether one was asked."""
    import signal

    if not is_running():
        return False
    try:
        pid = int(_lock_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid <= 1 or pid == os.getpid():
        return False
    if sys.platform == "win32":      # MD-181: os.kill is TerminateProcess here (no clean end); ask through a file, the loop ends at a safe point
        try:
            _stop_path().write_text(str(pid), encoding="utf-8")
        except OSError:
            return False
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def run(*, max_ticks: int | None = None, sleep: Callable[[float], None] = _sleep_until_asked_to_stop, interval: float = TICK_SECONDS, how: str = "manual") -> bool:
    """Tick until stopped (``max_ticks`` is for tests). Returns False at once when another background process is already running."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not filelock.try_lock(fd):
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())          # who holds the lock, so `uninstall` can stop exactly this process
        _clear_stop_request()                           # MD-181: a request left by an earlier stop must not end this new process at once
        from . import usage_hooks

        _refresh_login_item()                           # MD-171: an old login item (no --started-by) is brought up to date for the next login
        usage_hooks.background_started(how)             # MD-165: only the process that really runs reports, and only how it was started
        done = 0
        while max_ticks is None or done < max_ticks:
            try:
                scheduler.tick_all(now=time.time())
            except Exception:               # noqa: BLE001 - a bad tick must never end the process
                pass
            done += 1
            if stop_requested():
                break
            if max_ticks is None or done < max_ticks:
                sleep(interval)
                if stop_requested():
                    break
        _clear_stop_request()
        return True
    finally:
        os.close(fd)


def _refresh_login_item() -> None:
    try:
        scheduler.refresh_login_item()
    except Exception:                   # noqa: BLE001 - housekeeping must never stop the background process
        return


def ensure_running(*, how: str = "app_start") -> bool:
    """MD-165 self-healing: background sync is on (turned on by the first sync, not opted out) but nothing runs, so start it now.
    Called by every start of the app and every ``mdpipeline`` command. The lock keeps it to one process. Returns whether one was started."""
    from . import autostart
    from .schedulestore import SchedulerStore

    global started_this_run
    started_this_run = False
    state = SchedulerStore().read()["background"]
    if not state.get("enabled", True) or not state.get("announced") or is_running():
        return False
    started_this_run = autostart.start_now(how=how)
    return started_this_run


def wait_until_running(timeout: float | None = None) -> bool:
    """MD-166: a command that just started the process (self-heal) waits a moment for it to hold the lock, so its own status is true."""
    deadline = time.monotonic() + (STARTUP_WAIT_SECONDS if timeout is None else timeout)
    while not is_running():
        if time.monotonic() >= deadline:
            return False
        time.sleep(STARTUP_POLL_SECONDS)
    return True
