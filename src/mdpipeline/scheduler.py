"""Runs the pipelines that are due (MD-121). A scheduled run is an ordinary ``sync_pipeline`` with the ``schedule`` trigger:
the same plan, guard, conflict and never-delete-on-failure rules as a manual run. This module only decides *which* and *when*."""
from __future__ import annotations

from collections.abc import Callable

import time
from pathlib import Path

from . import autostart, notices, vault
from .brain import Brain
from .config import load_config
from .engine import SyncResult, sync_pipeline
from .log import ActivityLog
from .schedule import is_due
from .schedulestore import SchedulerStore, register_brain
from .status import locked_vault_run, parse_ts

NOT_A_RUN = ("busy", "skipped: paused")         # these did not try to sync, so they do not move the clock
WAITING = "blocked"                             # the guard stopped the last run: it waits for the person, it is not retried


def _meaningful_runs(brain: Brain, name: str) -> list[dict]:
    return [r for r in ActivityLog(brain).read(types={"run"}) if r.get("pipeline") == name and not r.get("dry_run")
            and r["result"] not in NOT_A_RUN]


def last_run_ts(brain: Brain, name: str) -> float | None:
    """When the pipeline last tried to sync (any outcome), or ``None`` if it never has."""
    runs = _meaningful_runs(brain, name)
    return parse_ts(runs[-1]["ts"]) if runs else None


def vault_opens() -> bool:
    """Whether the app's vault can be opened by this process right now (it may have been unlocked, or its keychain entry restored, since the last run)."""
    try:
        vault.default_vault().list()
    except vault.VaultError:
        return False
    return True


def run_due(brain: Brain, *, now: float | None = None) -> list[SyncResult]:
    """Sync every pipeline that is due at ``now``, once each. Returns the results (empty when nothing was due)."""
    now = time.time() if now is None else now
    config = load_config(brain)
    results: list[SyncResult] = []
    for pipeline in config.pipelines:
        runs = _meaningful_runs(brain, pipeline.name)
        last = parse_ts(runs[-1]["ts"]) if runs else None
        if runs and runs[-1]["result"] == WAITING:
            continue
        locked_before = bool(runs) and locked_vault_run(runs[-1])
        if is_due(pipeline, last, now) or (locked_before and vault_opens()):       # MD-161: unlocked since the last try: do not wait out the hour
            results.append(sync_pipeline(brain, pipeline.name, trigger="schedule", config=config))
    return results


def tick(brain: Brain, *, now: float | None = None) -> list[SyncResult]:
    """One beat for one brain: run what is due (this is also the catch-up after sleep), then look for problems worth a notice.

    The order matters: a catch-up run that succeeds resets the not-synced clock before any "stale" notice can be judged."""
    now = time.time() if now is None else now
    results = run_due(brain, now=now)
    notices.check(brain, now=now)
    return results


def background_enabled() -> bool:
    return bool(SchedulerStore().read()["background"].get("enabled", True))


def refresh_login_item() -> bool:
    """MD-171: bring an existing login item up to date (see ``autostart.refresh_if_outdated``), but only while background sync is on: with
    it off nothing is created or changed. Called when the app starts after an update and when the background process starts."""
    if not background_enabled():
        return False
    return autostart.refresh_if_outdated()


def set_background(enabled: bool) -> None:
    """Turn the background process's work on or off (the opt-out in Settings)."""
    with SchedulerStore().update() as data:
        data["background"]["enabled"] = bool(enabled)


GRACE_SECONDS = 7 * 24 * 3600       # MD-169 A1: a brain folder may be missing this long (unmounted drive, network share) before it is forgotten


def _folder_is_gone(path: str) -> bool:
    try:
        return not Path(path).is_dir()
    except OSError:                     # cannot tell (permissions, a stale mount): treat it as present
        return False


def _under_temp_dir(path: str) -> bool:
    import tempfile

    try:
        Path(path).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except (ValueError, OSError):
        return False
    return True


def serve_brains(now: float) -> list[str]:
    """MD-169 A1: the registered brains to tick now. A brain whose folder is missing is SKIPPED (no run, so no ``sync_failed``, no retry loop)
    and the moment it was first missing is recorded; the record is cleared the moment the folder is back, and syncing resumes by itself.
    A brain is unregistered (with its bookkeeping) only when its folder has been missing for ``GRACE_SECONDS`` without a break, or at once
    when it lives under the system temp dir (leftover test brains)."""
    store = SchedulerStore()
    registered = store.read()["brains"]
    gone = {path for path in registered if _folder_is_gone(path)}
    runtime = store.read()["runtime"]
    if not gone and not any("missing_since" in (runtime.get(path) or {}) for path in registered):
        return list(registered)
    serving: list[str] = []
    with store.update() as data:
        keep: list[str] = []
        for path in data["brains"]:
            entry = data["runtime"].get(path)
            if path not in gone:
                if isinstance(entry, dict):
                    entry.pop("missing_since", None)
                keep.append(path)
                serving.append(path)
                continue
            entry = data["runtime"].setdefault(path, {})
            since = entry.setdefault("missing_since", now)
            if _under_temp_dir(path) or now - since >= GRACE_SECONDS:
                data["runtime"].pop(path, None)
                continue
            keep.append(path)
        data["brains"] = keep
    return serving


def tick_all(*, now: float | None = None, should_stop: Callable[[], bool] | None = None) -> list[SyncResult]:
    """One beat for every registered brain. A brain that is gone or fails is skipped: it never stops the others.

    ``should_stop`` (MD-181, default: the background process's stop request): asked before each brain, so a stop request ends the beat at
    a safe point: the brain being synced finishes, the rest wait for the next process."""
    if not background_enabled():
        return []
    if should_stop is None:
        from .background import stop_requested as should_stop
    results: list[SyncResult] = []
    for path in serve_brains(time.time() if now is None else now):
        if should_stop is not None and should_stop():
            break
        try:
            brain = Brain(Path(path))
            if not brain.config_path.is_file():
                continue
            results.extend(tick(brain, now=now))
        except Exception:                   # noqa: BLE001 - one broken brain must not stop the background process
            continue
    return results


def _enable_user_service() -> None:
    """MD-165: also a systemd user service where there is one (best effort: the login item and the app's own restart remain)."""
    try:
        autostart.enable_user_service()
    except OSError:
        pass


ANNOUNCEMENT = ("Your files now stay current by themselves: a small background process syncs every hour. It keeps running when this "
                "window is closed and starts at login. You can change how often or turn it off any time.")


def ensure_background(*, acknowledge: bool = False) -> str | None:
    """Turn the background process on after a first sync. Returns the plain-words announcement the first time it is turned on, else ``None``.

    ``acknowledge``: the caller has already put the words in front of the person (a terminal that printed them). Otherwise they stay
    pending (``announce_pending``) until the person clicks OK, Change or Turn off, so a page reload never leaves an unannounced login item.

    Respects the opt-out (never turns itself back on) and says nothing when no login item could be made, so the words are always true."""
    store = SchedulerStore()
    state = store.read()["background"]
    if not state.get("enabled", True) or state.get("announced"):
        return None
    try:
        autostart.enable()
    except (autostart.AutostartError, OSError):
        return None
    _enable_user_service()
    autostart.start_now()
    with store.update() as data:
        data["background"]["announced"] = True
        data["background"]["acknowledged"] = bool(acknowledge)
    return ANNOUNCEMENT


def acknowledge_announcement() -> None:
    """The person has seen the words (OK, Change or Turn off)."""
    with SchedulerStore().update() as data:
        data["background"]["acknowledged"] = True


def turn_off_background() -> None:
    """The opt-out: stop doing scheduled work and remove the login item."""
    set_background(False)
    acknowledge_announcement()
    try:
        autostart.disable()
    except (autostart.AutostartError, OSError):
        pass
    try:
        autostart.disable_user_service()
    except OSError:
        pass


def turn_on_background() -> None:
    """Undo the opt-out: do scheduled work again and bring the login item back (and start the process now)."""
    set_background(True)
    acknowledge_announcement()
    try:
        autostart.enable()
        _enable_user_service()
        autostart.start_now()
    except (autostart.AutostartError, OSError):
        pass


def linger_notice_hidden() -> bool:
    return bool(SchedulerStore().read()["background"].get("linger_hidden"))


def hide_linger_notice() -> None:
    """The person closed the linger line on Home. Settings still shows it."""
    with SchedulerStore().update() as data:
        data["background"]["linger_hidden"] = True
