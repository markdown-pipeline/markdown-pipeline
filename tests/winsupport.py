"""Windows test support (MD-179): the `windows_unsupported(reason)` marker and the list of what it skipped."""
from __future__ import annotations

import os


def is_root() -> bool:
    """Whether the tests run as root (which ignores file permissions). Windows has no such concept."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def marker_reason(marker) -> str:
    """The reason a test cannot run on Windows. A marker without one is an error: skips must always be explained."""
    reason = (marker.args[0] if marker.args else marker.kwargs.get("reason", "")).strip()
    if not reason:
        raise ValueError("windows_unsupported needs a reason")
    return reason


def summary_lines(skipped: list[tuple[str, str]]) -> list[str]:
    """The block printed at the end of a Windows run: every test not run there, with why."""
    if not skipped:
        return []
    return [f"Not run on Windows ({len(skipped)}):", *(f"  {nodeid}: {reason}" for nodeid, reason in skipped)]
