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

from . import filelock
from .vault import data_dir

_thread_lock = threading.RLock()


def _defaults() -> dict:
    return {"background": {"enabled": True, "announced": False, "acknowledged": False}, "brains": [], "runtime": {}}


class SchedulerStore:
    def __init__(self) -> None:
        self._path: Path = data_dir() / "scheduler.json"

    def read(self) -> dict:
        """The stored data with defaults filled in. An unreadable file reads as the defaults: the scheduler must keep running."""
        data = _defaults()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
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
                data = self.read()
                working = copy.deepcopy(data)
                yield working
                tmp = self._path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps(working, indent=1), encoding="utf-8")
                os.replace(tmp, self._path)
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
