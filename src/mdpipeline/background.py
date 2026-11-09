"""The background process (MD-121): a small loop that keeps every registered brain current while the computer is on, with or
without the UI open. Started at login (see ``autostart``). One instance at a time, held by a lock in the app data directory.
A tick that fails is skipped; the process only ends when asked to."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable

from . import filelock, scheduler
from .vault import data_dir

TICK_SECONDS = 60
STARTUP_WAIT_SECONDS = 3.0          # MD-166: how long a command waits for a process it just started to take the lock
STARTUP_POLL_SECONDS = 0.2
RESTART_WAIT_SECONDS = 900           # MD-204: a replacement process waits this long for the old one to finish its sync and let go of the lock
SAFE_STOP_VERSION = "0.1.16"          # MD-204: the first version whose SIGTERM handler lets the sync it is on finish (older ones end at once)
UNKNOWN_VERSION = "unknown"          # a process that holds the lock but left no version record: it started before MD-204, so it is older than this code
RESTART_PENDING_SECONDS = 120          # MD-218: after a restart is requested, status says "restarting" this long (at least 60 s; a sync in progress finishes first), then "Restart it" again
NOT_RUNNING_GRACE_SECONDS = 600       # MD-212: with no process, a heartbeat younger than this is a restart in progress (10 ticks); far below the 24 h not-synced notice
found_down_at_start = False          # MD-212: set by the CLI before its self-heal: background was on but not running when this command began
started_this_run = False             # set by ensure_running: this command has just started the process
_terminate_requested = False         # MD-204: a SIGTERM arrived; the loop ends at the next safe point (after the brain being synced)


def _lock_path():
    return data_dir() / "background.lock"


def _stop_path():
    return data_dir() / "background.stop"


def _record_path():
    return data_dir() / "background.json"


def _restart_marker_path():
    return data_dir() / "background.restarting"


def mark_restart_requested(at: float | None = None, *, only_if_none: bool = False) -> None:
    """MD-218: a restart was asked for now (``update``, ``schedule background restart``). ``only_if_none``: a later command's own self-heal must not
    extend the window, so the first request anchors it. Housekeeping: never breaks the command."""
    path = _restart_marker_path()
    try:
        if only_if_none and path.exists():
            return
        path.write_text(json.dumps({"at": time.time() if at is None else at}), encoding="utf-8")
    except OSError:
        return


def restart_pending(now: float | None = None) -> bool:
    """MD-218: a restart was requested less than ``RESTART_PENDING_SECONDS`` ago and the running process is still an older version, so status says
    the restart is under way instead of asking for it. Past the window the "Restart it" line shows as before."""
    try:
        at = json.loads(_restart_marker_path().read_text(encoding="utf-8")).get("at")
    except (OSError, ValueError):
        return False
    now = time.time() if now is None else now
    return isinstance(at, (int, float)) and 0 <= now - at < RESTART_PENDING_SECONDS and is_outdated()


def restart_in_progress(now: float | None = None) -> bool:
    """MD-208: a restart was requested less than ``RESTART_PENDING_SECONDS`` ago and, for the moment, no process holds the lock (the old one has ended,
    the new one is waiting for it). Status says "restarting" then, never "not running". Past the window, a replacement that did not come shows as down."""
    try:
        at = json.loads(_restart_marker_path().read_text(encoding="utf-8")).get("at")
    except (OSError, ValueError):
        return False
    now = time.time() if now is None else now
    return isinstance(at, (int, float)) and 0 <= now - at < RESTART_PENDING_SECONDS and not is_running()


def record_heartbeat(now: float | None = None) -> None:
    """MD-212: the process is alive and ticking (the clock for "background isn't running"). Housekeeping: never breaks a tick."""
    from .schedulestore import SchedulerStore

    try:
        with SchedulerStore().update() as data:
            data["background"]["last_tick_at"] = time.time() if now is None else now
            data["background"].pop("not_running_shown", None)       # AC8: the outage is over; the next one is a new occurrence
    except Exception:                   # noqa: BLE001
        return


def not_running(now: float | None = None) -> bool:
    """MD-212 (AC7): background sync is on and announced, yet no process holds the lock and none ticked within the grace (or ever). A process
    asleep with the laptop still holds its lock, so sleeping never counts; the state clears by itself once a process runs again (AC8)."""
    from .schedulestore import SchedulerStore

    state = SchedulerStore().read()["background"]
    if not state.get("enabled", True) or not state.get("announced") or is_running() or restart_in_progress(now):
        return False
    last = state.get("last_tick_at")
    now = time.time() if now is None else now
    return not isinstance(last, (int, float)) or now - last > NOT_RUNNING_GRACE_SECONDS


def stop_requested() -> bool:
    """MD-181: someone asked this process to end (Windows has no clean signal, so the ask is a file the loop looks at).
    MD-204: on Linux and macOS a SIGTERM counts the same, so it never cuts a sync in half."""
    return _terminate_requested or _stop_path().exists()


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


def _version_numbers(version: str) -> tuple[int, ...] | None:
    numbers = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in numbers[:3]) if numbers else None


def running_version() -> str | None:
    """MD-204: the version of the background process that holds the lock now (``None`` when none runs). A process that left no record of its own
    (it started before this existed, or the record belongs to another process) is ``UNKNOWN_VERSION``."""
    if not is_running():
        return None
    try:
        pid = int(_lock_path().read_text(encoding="utf-8").strip())
        record = json.loads(_record_path().read_text(encoding="utf-8"))
        if record.get("pid") == pid and isinstance(record.get("version"), str) and record["version"]:
            return record["version"]
    except (OSError, ValueError, AttributeError):
        pass
    return UNKNOWN_VERSION


def is_outdated() -> bool:
    """MD-204: a background process runs, and its version is older than the installed one. Never true for a newer one (a checkout run beside an
    installed copy must not make the two restart each other)."""
    from . import __version__

    running = running_version()
    if running is None:
        return False
    if running == UNKNOWN_VERSION:
        return True
    old, installed = _version_numbers(running), _version_numbers(__version__)
    return bool(old and installed and old < installed)


def stops_safely(version: str | None) -> bool:
    """MD-204: whether a background process of this version finishes its sync when asked to stop (it has the SIGTERM handler)."""
    numbers, safe = _version_numbers(version or ""), _version_numbers(SAFE_STOP_VERSION)
    return bool(numbers and safe and version != UNKNOWN_VERSION and numbers >= safe)


def restart(*, refresh_request: bool = True) -> str:
    """MD-204: replace the background process with one of the installed version, between syncs: the old one is asked to stop (it finishes the brain it
    is on, if its version can), and a new one waits for the lock. Returns ``off`` / ``not-set-up`` / ``started`` / ``restarting`` (it finishes its sync
    first) / ``restarting-unsafe`` (an older process ends at once) / ``could-not-stop``."""
    from . import autostart

    from .schedulestore import SchedulerStore

    if not scheduler.background_enabled():
        return "off"
    if not is_running():
        if not SchedulerStore().read()["background"].get("announced"):          # it turns on by itself after the first sync; a restart must not start it earlier
            return "not-set-up"
        autostart.start_now(how="app_start")
        return "started"
    safe = stops_safely(running_version())
    if not stop():
        return "could-not-stop"
    mark_restart_requested(only_if_none=not refresh_request)          # MD-218: status says "restarting" for a while, not "Restart it"
    autostart.start_now(how="app_start", wait_for_lock=RESTART_WAIT_SECONDS)
    return "restarting" if safe else "restarting-unsafe"


def restart_if_outdated() -> bool:
    """MD-204: ``restart`` when the running process is older than the installed code and background sync is on. Returns whether it restarted."""
    if not scheduler.background_enabled() or not is_outdated():
        return False
    return restart(refresh_request=False).startswith("restarting")


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


def _take_lock(fd: int, wait_for_lock: float) -> bool:
    """Take the single-instance lock, waiting up to ``wait_for_lock`` seconds for a process that is finishing (MD-204: a restart)."""
    deadline = time.monotonic() + wait_for_lock
    while not filelock.try_lock(fd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)
    return True


def _install_sigterm_handler():
    """MD-204: SIGTERM ends the loop at a safe point instead of killing the process in the middle of a sync. Only where there is a real signal
    (Windows stops through a file, MD-181) and only in the main thread. Returns the previous handler to put back."""
    import signal

    global _terminate_requested
    _terminate_requested = False
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
        return None

    def ask_to_stop(signum, frame):       # noqa: ARG001
        global _terminate_requested
        _terminate_requested = True

    return signal.signal(signal.SIGTERM, ask_to_stop)


def run(*, max_ticks: int | None = None, sleep: Callable[[float], None] = _sleep_until_asked_to_stop, interval: float = TICK_SECONDS, how: str = "manual",
        wait_for_lock: float = 0) -> bool:
    """Tick until stopped (``max_ticks`` is for tests). Returns False at once when another background process is already running
    (``wait_for_lock``: wait that many seconds for it to end first, MD-204)."""
    import signal

    from . import __version__

    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    previous, held = None, False
    try:
        if not _take_lock(fd, wait_for_lock):
            return False
        held = True
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())          # who holds the lock, so `uninstall` can stop exactly this process
        try:
            _record_path().write_text(json.dumps({"pid": os.getpid(), "version": __version__}), encoding="utf-8")      # MD-204: which version this is
        except OSError:
            pass
        previous = _install_sigterm_handler()
        try:
            _restart_marker_path().unlink()             # MD-218: a new process is up: the restart that was asked for has happened
        except OSError:
            pass
        _clear_stop_request()                           # MD-181: a request left by an earlier stop must not end this new process at once
        from . import usage_hooks

        _refresh_login_item()                           # MD-171: an old login item (no --started-by) is brought up to date for the next login
        usage_hooks.background_started(how)             # MD-165: only the process that really runs reports, and only how it was started
        done = 0
        while max_ticks is None or done < max_ticks:
            record_heartbeat()
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
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        if held:                                         # never remove the record of the process that does hold the lock
            try:
                _record_path().unlink()
            except OSError:
                pass
        os.close(fd)


def _refresh_login_item() -> None:
    try:
        scheduler.refresh_login_item()
    except Exception:                   # noqa: BLE001 - housekeeping must never stop the background process
        return


def ensure_running(*, how: str = "app_start", restart_outdated: bool = True, via: str = "cli") -> bool:
    """MD-165 self-healing: background sync is on (turned on by the first sync, not opted out) but nothing runs, so start it now.
    Called by every start of the app and every ``mdpipeline`` command. The lock keeps it to one process. Returns whether one was started."""
    from . import autostart
    from .schedulestore import SchedulerStore

    global started_this_run
    started_this_run = False
    state = SchedulerStore().read()["background"]
    if not state.get("enabled", True) or not state.get("announced"):
        return False
    try:
        scheduler.ensure_login_item(via)       # MD-212: a login item that went missing comes back, so the next login starts it again
    except Exception:                       # noqa: BLE001 - housekeeping never breaks the command
        pass
    if is_running():
        if restart_outdated:                # MD-204: the first command after an update replaces a process that still runs the old code
            try:
                restart_if_outdated()
            except Exception:               # noqa: BLE001 - housekeeping never breaks the command
                pass
        return False
    started_this_run = autostart.start_now(how=how)
    if started_this_run:                    # MD-212 (H-BOOT-2): it was on but dead, and this command put it back
        from . import usage_hooks

        usage_hooks.background_self_healed("process", via)
    return started_this_run


def wait_until_running(timeout: float | None = None) -> bool:
    """MD-166: a command that just started the process (self-heal) waits a moment for it to hold the lock, so its own status is true."""
    deadline = time.monotonic() + (STARTUP_WAIT_SECONDS if timeout is None else timeout)
    while not is_running():
        if time.monotonic() >= deadline:
            return False
        time.sleep(STARTUP_POLL_SECONDS)
    return True
