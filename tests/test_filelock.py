"""MD-179 (Windows W1): one cross-process file lock for every lock in the app, with a POSIX and a Windows way to take it."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from mdpipeline import filelock

SRC = Path(filelock.__file__).parent
TESTS = Path(__file__).parent


def _open(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def test_only_filelock_imports_fcntl_so_the_app_imports_on_windows() -> None:
    offenders = []
    for path in [*SRC.glob("*.py"), TESTS / "bg_helper.py"]:
        if path.name == "filelock.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
            if "fcntl" in names:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_second_holder_in_the_same_process_is_refused(tmp_path: Path) -> None:
    first, second = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        assert filelock.try_lock(first) is True
        assert filelock.try_lock(second) is False
    finally:
        os.close(first)
        os.close(second)


def test_lock_can_be_taken_again_after_unlock(tmp_path: Path) -> None:
    first, second = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        filelock.try_lock(first)
        filelock.unlock(first)
        assert filelock.try_lock(second) is True
    finally:
        os.close(first)
        os.close(second)


def test_lock_is_released_when_the_descriptor_is_closed(tmp_path: Path) -> None:
    first = _open(tmp_path / "l")
    filelock.try_lock(first)
    os.close(first)
    second = _open(tmp_path / "l")
    try:
        assert filelock.try_lock(second) is True
    finally:
        os.close(second)


def test_blocking_lock_waits_for_the_holder_to_let_go(tmp_path: Path) -> None:
    first, second = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        filelock.try_lock(first)
        threading.Timer(0.3, filelock.unlock, args=(first,)).start()
        started = time.monotonic()
        assert filelock.try_lock(second, blocking=True) is True
        assert time.monotonic() - started >= 0.2
    finally:
        os.close(first)
        os.close(second)


def test_lock_does_not_block_reading_or_writing_the_file_content(tmp_path: Path) -> None:
    """background.py writes its pid into the lock file while holding it, and `uninstall` reads it from another process."""
    path = tmp_path / "l"
    holder = _open(path)
    try:
        filelock.try_lock(holder)
        os.ftruncate(holder, 0)
        os.write(holder, b"4242")
        assert path.read_text() == "4242"
    finally:
        os.close(holder)


_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from mdpipeline import filelock
    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
    print("held" if filelock.try_lock(fd) else "busy", flush=True)
    time.sleep(60)
    """
)


def _taken_within(fd: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if filelock.try_lock(fd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _child_holding(path: Path) -> subprocess.Popen:
    child = subprocess.Popen([sys.executable, "-c", _CHILD, str(path)], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


def test_another_process_holding_the_lock_makes_us_busy_and_its_death_frees_it(tmp_path: Path) -> None:
    path = tmp_path / "l"
    child = _child_holding(path)
    mine = _open(path)
    try:
        assert filelock.try_lock(mine) is False
        child.kill()
        child.wait()
        assert _taken_within(mine, seconds=5) is True        # Windows lets go of a dead process's lock a moment after it ends
    finally:
        os.close(mine)
        if child.poll() is None:
            child.kill()


class FakeMsvcrt:
    """Records what the Windows backend asks the C runtime to do; ``busy`` makes the lock call fail like a held lock."""

    LK_NBLCK, LK_LOCK, LK_UNLCK = 2, 1, 0

    def __init__(self, busy: bool = False) -> None:
        self.busy, self.calls, self.pos = busy, [], 0

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((self.pos, mode, nbytes))
        if self.busy and mode != self.LK_UNLCK:
            raise OSError(13, "Permission denied")


def _windows(busy: bool = False):
    msvcrt = FakeMsvcrt(busy)
    seeks: list[int] = []

    def lseek(fd: int, pos: int, how: int) -> int:
        """Like the real ``os.lseek``: it returns the NEW position (not the old one)."""
        msvcrt.pos = msvcrt.pos + pos if how == os.SEEK_CUR else pos
        seeks.append(msvcrt.pos)
        return msvcrt.pos

    return filelock.WindowsLock(msvcrt, lseek), msvcrt, seeks


def test_windows_lock_takes_one_byte_far_from_the_content_and_restores_the_position() -> None:
    lock, msvcrt, seeks = _windows()
    assert lock.try_lock(7, blocking=False) is True
    assert msvcrt.calls == [(filelock.LOCK_OFFSET, FakeMsvcrt.LK_NBLCK, 1)]
    assert msvcrt.pos == 0                                  # the caller's position (pid writes at 0) is back where it was


def test_windows_lock_reports_busy_when_the_c_runtime_refuses() -> None:
    lock, msvcrt, _ = _windows(busy=True)
    assert lock.try_lock(7, blocking=False) is False
    assert msvcrt.pos == 0


def test_windows_blocking_lock_retries_until_it_gets_it() -> None:
    lock, msvcrt, _ = _windows(busy=True)
    attempts = []
    original = msvcrt.locking

    def locking(fd: int, mode: int, nbytes: int) -> None:
        attempts.append(mode)
        if len(attempts) == 3:
            msvcrt.busy = False
        original(fd, mode, nbytes)

    msvcrt.locking = locking
    assert lock.try_lock(7, blocking=True, sleep=lambda s: None) is True
    assert len(attempts) == 3


def test_windows_unlock_releases_the_same_byte() -> None:
    lock, msvcrt, _ = _windows()
    lock.try_lock(7, blocking=False)
    lock.unlock(7)
    assert msvcrt.calls[-1] == (filelock.LOCK_OFFSET, FakeMsvcrt.LK_UNLCK, 1)


def test_windows_lock_keeps_a_position_that_is_not_zero() -> None:
    lock, msvcrt, _ = _windows()
    msvcrt.pos = 123
    lock.try_lock(7, blocking=False)
    lock.unlock(7)
    assert msvcrt.pos == 123
