"""Scheduled sync rules (MD-121): when a pipeline is due. Pure functions, no I/O: the clock and the last run come in as arguments."""
from __future__ import annotations

from .config import Pipeline

_SECONDS = {"15m": 900, "1h": 3600, "6h": 21600, "daily": 86400}


def interval_seconds(value: str) -> int | None:
    """The length of an interval in seconds; ``None`` for ``off``."""
    return _SECONDS.get(value)


def next_sync(pipeline: Pipeline, last_run: float | None, now: float) -> float | None:
    """When the next scheduled sync is: ``None`` when there will be none (off, paused, or no first sync yet).

    A pipeline that is overdue (the computer slept) is due *now*: one catch-up run, never a backlog. So is one whose last run is stamped
    in the future (MD-170: the clock was ahead and has been corrected): it must not wait for the wall clock to catch up."""
    seconds = interval_seconds(pipeline.schedule)
    if seconds is None or pipeline.paused or last_run is None:
        return None
    if last_run > now:
        return now
    return max(last_run + seconds, now)


def is_due(pipeline: Pipeline, last_run: float | None, now: float) -> bool:
    """Whether a scheduled run should start now. Scheduled sync starts after the first sync (``last_run`` is ``None`` before it)."""
    seconds = interval_seconds(pipeline.schedule)
    if seconds is None or pipeline.paused or last_run is None:
        return False
    if last_run > now:              # MD-170: stamped in the future (the clock moved back): do not wait the skew out, sync on this tick
        return True
    return now - last_run >= seconds
