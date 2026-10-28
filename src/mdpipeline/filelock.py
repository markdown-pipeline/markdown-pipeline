"""One cross-process file lock for the whole app (MD-179): ``flock`` on Linux and macOS, ``msvcrt.locking`` on Windows.

Both are exclusive, are released when the descriptor is closed or the process dies, and refuse a second holder even inside one process
(a different descriptor of the same file). Every module takes its lock through here, so only this file knows which platform it is on.
"""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable

LOCK_OFFSET = 1 << 30            # Windows locks are mandatory: lock one byte far past the content so reading or writing the file still works
RETRY_SECONDS = 0.05


class WindowsLock:
    """The Windows way: one byte of the file at ``LOCK_OFFSET``. ``msvcrt`` and ``lseek`` are passed in so it can be tested anywhere."""

    def __init__(self, msvcrt, lseek: Callable[[int, int, int], int] = os.lseek) -> None:
        self._msvcrt, self._lseek = msvcrt, lseek

    def _at_offset(self, fd: int, mode: int) -> None:
        previous = self._lseek(fd, 0, os.SEEK_CUR)          # os.lseek returns the NEW position, so ask for the current one first
        self._lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        try:
            self._msvcrt.locking(fd, mode, 1)
        finally:
            self._lseek(fd, previous, os.SEEK_SET)           # the caller's file position (e.g. a pid written at 0) is untouched

    def try_lock(self, fd: int, *, blocking: bool, sleep: Callable[[float], None] = time.sleep) -> bool:
        while True:
            try:
                self._at_offset(fd, self._msvcrt.LK_NBLCK)
                return True
            except OSError:
                if not blocking:
                    return False
                sleep(RETRY_SECONDS)

    def unlock(self, fd: int) -> None:
        self._at_offset(fd, self._msvcrt.LK_UNLCK)


class PosixLock:
    """The Linux and macOS way: ``flock`` on the whole file."""

    def __init__(self, fcntl) -> None:
        self._fcntl = fcntl

    def try_lock(self, fd: int, *, blocking: bool, sleep: Callable[[float], None] = time.sleep) -> bool:
        try:
            self._fcntl.flock(fd, self._fcntl.LOCK_EX | (0 if blocking else self._fcntl.LOCK_NB))
        except OSError:                                      # BlockingIOError when held; any other refusal also means "not taken"
            return False
        return True

    def unlock(self, fd: int) -> None:
        self._fcntl.flock(fd, self._fcntl.LOCK_UN)


def _platform_lock():
    if sys.platform == "win32":
        import msvcrt

        return WindowsLock(msvcrt)
    import fcntl

    return PosixLock(fcntl)


_lock = _platform_lock()


def try_lock(fd: int, *, blocking: bool = False) -> bool:
    """Take the exclusive lock on the open descriptor ``fd``. Returns False when somebody else holds it (never raises for that)."""
    return _lock.try_lock(fd, blocking=blocking)


def unlock(fd: int) -> None:
    """Let go of the lock. Closing the descriptor does the same, and so does the process dying."""
    _lock.unlock(fd)
