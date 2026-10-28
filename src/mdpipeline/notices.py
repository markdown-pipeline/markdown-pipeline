"""Notices for scheduled sync (MD-121): a sync that fails twice in a row, a source that is missing, a pipeline not synced for 24
awake hours, a guard-blocked run waiting for the person. One notice per problem, not per run.

A problem raises its notice once (an OS notification, a "Needs you" item, the ``notice_shown`` event) and keeps the item until the
problem is gone; if it comes back it is raised again. The OS notification is the first thing that can make the system ask for
permission to notify, so permission is asked at the first real notice, never during the first run (AC8).

"Not synced for 24 h" counts only the time the app was awake and watching (ticks no further apart than ``MAX_TICK_GAP``): a laptop
closed over a weekend adds nothing, and the catch-up run that follows resets the clock before any notice could fire (AC9).
"""
from __future__ import annotations


import subprocess
import sys
import time
from shutil import which

from . import usage
from .brain import Brain
from .config import Pipeline, load_config
from .log import ActivityLog
from .schedulestore import SchedulerStore
from .status import locked_vault_run, SUCCESS_RESULTS, parse_ts

MAX_TICK_GAP = 300                 # seconds: a longer silence between two ticks means the computer slept (or the app was off)
STALE_AFTER = 24 * 3600            # awake seconds without a successful sync
MISSING_CATEGORIES = ("source-missing", "site-not-found")
NOT_A_RUN = ("busy", "skipped: paused")

_TEXT = {
    "sync_failed": "{p} could not sync twice in a row. Nothing was deleted. Open Markdown Pipeline to see why.",
    "source_missing": "A source of {p} is missing, so nothing was changed. Open Markdown Pipeline to fix it.",
    "stale": "{p} has not synced for over 24 hours of use. Nothing was deleted. Open Markdown Pipeline to see why.",
    "guard_blocked": "{p} is waiting for your OK: its next sync would change many files, so nothing was changed yet.",
}


def os_notify(title: str, body: str) -> bool:
    """Show an OS notification, best effort: returns whether one was handed to the system. Never raises."""
    try:
        if sys.platform == "darwin":
            script = 'display notification "{}" with title "{}"'.format(body.replace('"', "'"), title.replace('"', "'"))
            command = ["osascript", "-e", script]
        elif which("notify-send"):
            command = ["notify-send", "--app-name=Markdown Pipeline", title, body]
        else:
            return False
        return subprocess.run(command, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _runtime(data: dict, brain: Brain) -> dict:
    return data["runtime"].setdefault(str(brain.root), {})


def awake_seconds(brain: Brain, name: str) -> float:
    """Awake seconds counted for the pipeline since its last successful sync."""
    entry = ((SchedulerStore().read()["runtime"].get(str(brain.root)) or {}).get("awake") or {}).get(name) or {}
    return entry.get("seconds", 0)


def active_notices(brain: Brain) -> list[dict]:
    """The notices that are up now: ``{"pipeline", "kind", "since"}`` (the "Needs you" items)."""
    notices = (SchedulerStore().read()["runtime"].get(str(brain.root)) or {}).get("notices") or {}
    return list(notices.values())


def _watched(pipeline: Pipeline) -> bool:
    return not pipeline.paused


def _failed(run: dict) -> bool:
    """A real failure. A locked vault (MD-161) waits for the person to unlock: it is shown as "needs you", never as a failure."""
    return run["result"] == "error" and not locked_vault_run(run)


def _problems(runs: list[dict], pipeline: Pipeline, awake: float) -> list[str]:
    found: list[str] = []
    last = runs[-1] if runs else None
    if last is not None and last["result"] == "blocked":
        found.append("guard_blocked")
    missing = last is not None and last["result"] == "error" and any(e.get("category") in MISSING_CATEGORIES for e in last.get("errors") or [])
    if missing:
        found.append("source_missing")
    elif len(runs) >= 2 and _failed(runs[-1]) and _failed(runs[-2]):
        found.append("sync_failed")
    if pipeline.schedule != "off" and awake >= STALE_AFTER and not found:       # a failure or a waiting decision already says why: one card, one notification
        found.append("stale")
    return found


def check(brain: Brain, *, now: float | None = None) -> list[dict]:
    """One look at the brain: advance the awake clocks, raise notices for new problems, drop those that are gone.

    Returns the notices that are up. Call it on every tick of the background process, after the due pipelines ran."""
    now = time.time() if now is None else now
    config = load_config(brain)
    all_runs = [r for r in ActivityLog(brain).read(types={"run"}) if not r.get("dry_run") and r["result"] not in NOT_A_RUN]
    raised: list[tuple[str, str]] = []
    with SchedulerStore().update() as data:
        rt = _runtime(data, brain)
        last_tick = rt.get("last_tick")
        gap = None if last_tick is None else now - last_tick
        counted = gap if gap is not None and 0 < gap <= MAX_TICK_GAP else 0
        rt["last_tick"] = now
        awake = rt.setdefault("awake", {})
        old = rt.get("notices") or {}
        current: dict[str, dict] = {}
        for pipeline in config.pipelines:
            if not _watched(pipeline):
                awake.pop(pipeline.name, None)
                continue
            runs = [r for r in all_runs if r.get("pipeline") == pipeline.name]
            success = next((parse_ts(r["ts"]) for r in reversed(runs) if r["result"] in SUCCESS_RESULTS), None)
            entry = awake.get(pipeline.name)
            if entry is None or entry.get("success_ts") != success:
                entry = awake[pipeline.name] = {"success_ts": success, "seconds": 0}
            elif success is not None:
                entry["seconds"] += counted
            for kind in _problems(runs, pipeline, entry["seconds"] if success is not None else 0):
                key = f"{pipeline.name}|{kind}"
                current[key] = old.get(key) or {"pipeline": pipeline.name, "kind": kind, "since": now}
                if key not in old:
                    raised.append((pipeline.name, kind))
        rt["notices"] = current
    for name, kind in raised:
        os_notify("Markdown Pipeline", _TEXT[kind].format(p=f'"{name}"'))
        usage.emit("notice_shown", kind=kind)
    return list(current.values())
