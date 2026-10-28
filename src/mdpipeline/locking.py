"""A brain-wide lock so two overlapping syncs (CLI, MCP, UI) never interleave."""
from __future__ import annotations

import os
from contextlib import contextmanager

from . import filelock
from .brain import Brain


class BusyError(Exception):
    """Another sync holds the brain lock."""


@contextmanager
def brain_lock(brain: Brain):
    """Hold the brain lock without waiting; raises BusyError if somebody else has it."""
    fd = os.open(brain.config_dir / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not filelock.try_lock(fd):
            raise BusyError("another sync is running")
        yield
    finally:
        os.close(fd)  # closing releases the flock


def is_locked(brain: Brain) -> bool:
    """Whether a sync currently holds the brain lock (used to show a 'syncing' status)."""
    path = brain.config_dir / "lock"
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if filelock.try_lock(fd):
            return False
        return True
    finally:
        os.close(fd)
