"""Retry a file operation that Windows refuses for a moment (MD-226).

On Windows, replacing or opening a file that another thread or process has open fails with ``PermissionError`` (WinError 5 or 32) and succeeds a few
milliseconds later. POSIX never refuses a replace or an unlink for an open file, so there nothing is retried: a ``PermissionError`` on Linux or macOS is real (a read-only folder, a missing right) and is raised at once, as before MD-226/MD-228 (MD-229). The wait is bounded.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

BUDGET_SECONDS = 1.0           # total pause before giving up
FIRST_PAUSE = 0.01             # doubles each time, capped at MAX_PAUSE


def retry_enabled() -> bool:
    """True where a refused replace/unlink can be a passing "file is open" refusal: Windows only (MD-229). Tests that simulate Windows patch this."""
    return os.name == "nt"


def retry_permission(operation: Callable[[], T]) -> T:
    """Run ``operation``; on Windows, on ``PermissionError`` wait a little and try again, for about ``BUDGET_SECONDS`` in total, then raise the last error. Elsewhere run it once."""
    if not retry_enabled():
        return operation()
    waited, pause = 0.0, FIRST_PAUSE
    while True:
        try:
            return operation()
        except PermissionError:
            if waited >= BUDGET_SECONDS:
                raise
            step = min(pause, 0.1, BUDGET_SECONDS - waited) or 0.01
            time.sleep(step)
            waited += step
            pause *= 2


def replace(src, dst) -> None:
    """``os.replace`` that waits out a short Windows refusal (the destination is open in an editor, a reader or another thread); POSIX never refuses."""
    retry_permission(lambda: os.replace(src, dst))


def publish(tmp, dst) -> None:
    """Move the finished temp file ``tmp`` over ``dst`` like :func:`replace`; if it still fails, delete ``tmp`` and raise: ``dst`` keeps its old content."""
    try:
        replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
