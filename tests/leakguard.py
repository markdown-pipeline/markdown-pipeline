"""MD-169: what a test run must never leave behind.

* A ``python -m mdpipeline background`` process from this repo's own interpreter (a test that triggers the self-start, or runs the
  command, and forgets to stop it). The owner's installed app runs from another interpreter and is neither judged nor killed.
* A change to the real user's home: a brain added to the real ``scheduler.json``, a new or rewritten login item in
  ``~/.config/autostart``, a new file in the real data directory. Counters the running installed app rewrites (``runtime``) are ignored.

Wired in conftest.py (per test, so the failing test is named). Unit-tested in test_leak_guard.py.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

OWN_PYTHON = (os.path.realpath(sys.executable), sys.executable)
MARKER = ["-m", "mdpipeline", "background"]


def _argv(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _is_ours(argv: list[str]) -> bool:
    if not argv or argv[0] not in OWN_PYTHON:
        return False
    return any(argv[i:i + 3] == MARKER for i in range(len(argv) - 2))


def background_pids() -> set[int]:
    """Every running ``<this interpreter> -m mdpipeline background`` process (Linux ``/proc``; elsewhere nothing is seen)."""
    proc = Path("/proc")
    if not proc.is_dir():
        return set()
    found: set[int] = set()
    for entry in proc.iterdir():
        if entry.name.isdigit() and _is_ours(_argv(int(entry.name))):
            found.add(int(entry.name))
    return found


def leaked_since(before: set[int]) -> set[int]:
    """The background processes that were not running at the ``before`` baseline."""
    return background_pids() - before


def kill(pids) -> None:
    """Stop leaked processes (SIGTERM, then SIGKILL for any that ignore it) so a failing run does not make the next one worse."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        alive = [pid for pid in pids if pid in background_pids()]
        for pid in alive:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        if not alive:
            return
        _wait_gone(alive)


def _wait_gone(pids, seconds: float = 2.0) -> None:
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end and any(pid in background_pids() for pid in pids):
        time.sleep(0.05)


def real_state(home: Path) -> dict:
    """What the real home holds that tests must not touch (read only)."""
    data = home / ".local" / "share" / "mdpipeline"
    brains: list[str] = []
    try:
        brains = list(json.loads((data / "scheduler.json").read_text(encoding="utf-8")).get("brains", []))
    except (OSError, ValueError, AttributeError):
        pass
    autostart = home / ".config" / "autostart"
    items = {}
    if autostart.is_dir():
        for path in sorted(autostart.iterdir()):
            try:
                items[path.name] = path.read_bytes() if path.is_file() else b""
            except OSError:
                items[path.name] = b""
    files = sorted(p.name for p in data.iterdir() if not p.name.endswith(".tmp")) if data.is_dir() else []      # .tmp: an atomic save in flight
    return {"brains": brains, "login_items": items, "data_files": files}


def real_state_changes(before: dict, after: dict) -> list[str]:
    """Plain sentences for each difference (empty = nothing changed)."""
    out: list[str] = []
    for brain in after["brains"]:
        if brain not in before["brains"]:
            out.append(f"scheduler.json: brain registered in the real data dir: {brain}")
    for brain in before["brains"]:
        if brain not in after["brains"]:
            out.append(f"scheduler.json: brain removed from the real data dir: {brain}")
    for name, content in after["login_items"].items():
        if name not in before["login_items"]:
            out.append(f"~/.config/autostart: new file {name}")
        elif before["login_items"][name] != content:
            out.append(f"~/.config/autostart: {name} was rewritten")
    for name in before["login_items"]:
        if name not in after["login_items"]:
            out.append(f"~/.config/autostart: {name} was removed")
    for name in sorted(set(after["data_files"]) - set(before["data_files"])):
        out.append(f"real data dir: new file {name}")
    return out
