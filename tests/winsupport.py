"""Windows test support (MD-179): the `windows_unsupported(reason)` marker and the list of what it skipped."""
from __future__ import annotations

import os


HARD_KILL_EXIT = 9 if os.name == "nt" else -9
"""The exit code of a process killed with ``os.kill(os.getpid(), 9)``: -SIGKILL on POSIX; Windows has no signals, so TerminateProcess sets it to 9."""


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


def set_home(monkeypatch, path) -> None:
    """Point the user's home at ``path``: HOME for POSIX, USERPROFILE for Windows (``Path.home()`` and ``expanduser`` read that one there)."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


STARTS_AT_LOGIN = "starts when you sign in to Windows" if os.name == "nt" else "starts at login"
"""How the announcement says the background process starts (MD-181 gave Windows its own words)."""
SETTINGS_STARTS = "(starts when you sign in to Windows)" if os.name == "nt" else "(starts when you log in to your desktop)"


def default_folder(name: str) -> str:
    """The first-run default output folder the app suggests for ``name``: ``~/markdown-pipeline/<name>``, or the full Windows path (MD-223)."""
    if os.name == "nt":
        return os.path.expanduser("~") + "\\markdown-pipeline\\" + name
    return "~/markdown-pipeline/" + name
