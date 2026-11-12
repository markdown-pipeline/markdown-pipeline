"""The scheduler's settings and runtime bookkeeping: one JSON file in the app data directory, never inside a brain (MD-121).

Settings (the person's): ``background`` on/off, whether the first-time announcement was shown, the registered brains.
Runtime (the background process's): per brain the awake-time counters and the notices already raised.
Every change is a read-modify-write under one file lock shared by the UI, the CLI and the background process.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import threading
from pathlib import Path

from . import filelock, fsretry
from .vault import data_dir

_thread_lock = threading.RLock()
_held = threading.local()          # depth of update() blocks this thread is inside (its reads must not take the file lock a second time)


class ScheduleSaveError(OSError):
    """The schedule could not be saved right now. The message is for the person: what happened and what to do; the old schedule is untouched."""


_SAVE_FAILED = ("Markdown Pipeline could not save the schedule just now because another program is using its settings file. "
                "Your previous schedule is unchanged. Please try again in a moment.")


def _no_permission(folder) -> str:
    """Linux/macOS (MD-229 part B): the settings folder is not writable for this user; marketing's sentence, verbatim."""
    return (f"Markdown Pipeline couldn't save the schedule because it doesn't have permission to write to its settings folder, {folder}. "
            "Your previous schedule is unchanged. Make that folder writable for your user, then save again.")


def _defaults() -> dict:
    return {"background": {"enabled": True, "announced": False, "acknowledged": False}, "brains": [], "runtime": {}}


class SchedulerStore:
    def __init__(self) -> None:
        self._path: Path = data_dir() / "scheduler.json"

    def read(self) -> dict:
        """The stored data with defaults filled in. An unreadable file reads as the defaults: the scheduler must keep running.

        MD-226: a read takes the same lock as an update (unless this thread is inside an update already), so on Windows a read never holds the file open
        while ``os.replace`` runs, and never sees it half-written.
        """
        if getattr(_held, "depth", 0):
            return self._read_file()
        try:
            with _thread_lock:
                fd = os.open(self._path.with_name("scheduler.lock"), os.O_RDWR)       # never created by a read: viewing writes nothing
                try:
                    filelock.try_lock(fd, blocking=True)
                    return self._read_file()
                finally:
                    os.close(fd)
        except OSError:                                    # no lock file yet (nothing was ever saved), or it cannot be taken: read as before
            return self._read_file()

    def _read_file(self) -> dict:
        data = _defaults()
        try:
            raw = json.loads(fsretry.retry_permission(lambda: self._path.read_text(encoding="utf-8")))     # Windows may refuse a read for a moment
        except (OSError, ValueError):
            return data
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key == "background" and isinstance(value, dict):
                    data["background"].update(value)
                elif key in data and isinstance(value, type(data[key])):
                    data[key] = value
        return data

    @contextlib.contextmanager
    def update(self):
        """``with store.update() as data:`` change ``data``; it is saved when the block ends without an error."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _thread_lock:
            fd = os.open(self._path.with_name("scheduler.lock"), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                filelock.try_lock(fd, blocking=True)
                _held.depth = getattr(_held, "depth", 0) + 1
                try:
                    data = self._read_file()
                    working = copy.deepcopy(data)
                    yield working
                finally:
                    _held.depth -= 1
                tmp = self._path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                try:
                    tmp.write_text(json.dumps(working, indent=1), encoding="utf-8")
                    fsretry.retry_permission(lambda: os.replace(tmp, self._path))
                except PermissionError as e:
                    tmp.unlink(missing_ok=True)
                    raise ScheduleSaveError(_SAVE_FAILED if fsretry.retry_enabled() else _no_permission(self._path.parent)) from e
            finally:
                os.close(fd)


def register_brain(brain) -> None:
    """Remember the brain so the background process serves it. Idempotent. Never raises: bookkeeping must not fail a sync."""
    try:
        path = str(brain.root)
        if path not in SchedulerStore().read()["brains"]:
            with SchedulerStore().update() as data:
                if path not in data["brains"]:
                    data["brains"].append(path)
    except OSError:
        return
