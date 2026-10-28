"""What a long first-run step is doing right now (MD-153), so the screen can say it instead of a static "Looking at Jira…".

In memory only, one current step, nothing stored and nothing sent. A step is active between :func:`start` and :func:`finish`;
:func:`report` outside a step is ignored, so a background sync never shows up on the first-run screen."""
from __future__ import annotations

import threading
from typing import Callable

_lock = threading.Lock()
_state: dict | None = None
_listener: Callable[[dict], None] | None = None


def _text(s: dict) -> str:
    noun, source, done, total = s["noun"], s["source"], s["done"], s["total"]
    where = f" {'in' if s['phase'] == 'listing' else 'from'} {source}" if source else ""
    if s["phase"] == "listing":
        return f"Finding {noun}{where}… {done} so far"
    if s["phase"] == "writing":
        return f"Writing files… {done} of {total}"
    return f"Reading {noun}{where}… {done} of {total}"


def _publish() -> None:
    if _state is not None:
        _state["text"] = _text(_state)
        if _listener:
            _listener(dict(_state))


def start(phase: str, total: int | None = None, *, noun: str = "files", source: str = "") -> None:
    """Begin a step (clears the previous one)."""
    global _state
    with _lock:
        _state = {"phase": phase, "done": 0, "total": total, "noun": noun, "source": source, "text": ""}
        _publish()


def report(phase: str, done: int, total: int | None = None) -> None:
    """Move the active step to ``phase`` with ``done`` items (and ``total`` when known). Ignored when no step is active."""
    with _lock:
        if _state is None:
            return
        _state.update(phase=phase, done=done)
        if total is not None:
            _state["total"] = total
        _publish()


def finish() -> None:
    global _state
    with _lock:
        _state = None


def current() -> dict | None:
    with _lock:
        return dict(_state) if _state is not None else None


def listen(callback: Callable[[dict], None] | None) -> None:
    """Test hook: called with a copy of the state after every change."""
    global _listener
    _listener = callback
