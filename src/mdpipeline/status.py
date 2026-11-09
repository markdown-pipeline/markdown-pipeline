"""Pipeline and brain status derived from the config, the state and the activity log."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .brain import Brain
from .config import load_config
from .destinations import bind_target, credential_conflict_message, credential_conflict_of, describe_destination, destination_key
from .log import ActivityLog
from .schedule import next_sync
from .state import State
from .target import TargetError

SUCCESS_RESULTS = ("ok", "conflicts")


def parse_ts(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


def _status_from_run(run: dict | None) -> str:
    if run is None:
        return "never"
    return {"ok": "ok", "conflicts": "conflicts", "error": "error", "blocked": "blocked", "cancelled": "blocked"}.get(
        run["result"], "ok")


def _target_state(brain: Brain, pipeline) -> tuple[dict, State]:
    """The pipeline's destination for display, and the state read from it. An unreachable target is reported here, never raised."""
    info = describe_destination(brain, pipeline)
    try:
        bound = bind_target(brain, pipeline)
        bound.target.begin()                # a repository target re-reads its head: status is never a stale snapshot
        return info, State.load(bound)
    except TargetError as exc:             # the repository cannot be read right now: status still answers (the log is local)
        info["error"], info["category"] = str(exc), exc.category
        return info, State(brain)


VAULT_WAITING = ("vault-locked", "vault-unsafe")        # the person's to fix: unlock an old passphrase vault once / chmod the secrets file (MD-163)
NEEDS_YOU_CATEGORIES = ("source-missing", *VAULT_WAITING)      # MD-155: a locked vault is the person's to unlock, like a missing folder


def locked_vault_run(run: dict | None) -> bool:
    """MD-161: the run stopped ONLY because the vault was locked (the category is on the run, or on every error it lists)."""
    if not run or run.get("result") != "error":
        return False
    errors = run.get("errors") or []
    if errors:
        return all(e.get("category") in VAULT_WAITING for e in errors)
    return run.get("category") in VAULT_WAITING


def only_missing_sources(run: dict | None) -> bool:
    """MD-136 AC3: the run failed ONLY because source folders are gone. Nothing was deleted and the person only has to say where the folder went,
    so the screens say "needs you" (amber) instead of "error" (red). Any other failure next to it keeps the error."""
    errors = (run or {}).get("errors") or []
    if bool(run) and run.get("result") == "error" and not errors and run.get("category") in VAULT_WAITING:
        return True                # MD-155: a locked vault stopped the run before any source (nothing was changed; the person only has to unlock)
    return bool(run) and run.get("result") == "error" and bool(errors) and all(e.get("category") in NEEDS_YOU_CATEGORIES for e in errors)


RESTART_COMMAND = "mdpipeline schedule background restart"       # MD-212: confirmed in cli.py (starts the process when it is on and not running)


def iso_utc(ts: float | None) -> str | None:
    """MD-212: an epoch time as ISO 8601 UTC (``2026-11-06T10:00:00Z``), for the agent reading sync_status."""
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_update_phrase(ts: float | None, now: float) -> str:
    """MD-212: "3 hours ago (2026-11-06 08:15)" - the relative time first, the local date and time in brackets. ``never`` when nothing synced."""
    if ts is None:
        return "never"
    seconds = max(0, now - ts)                                  # never negative after a clock jump
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = int(seconds // size)
            relative = f"{n} {unit}{'' if n == 1 else 's'} ago"
            break
    else:
        relative = "less than a minute ago"
    return f"{relative} ({datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')})"


def background_down_cli_text(ts: float | None, now: float) -> str:
    """MD-212 (AC7, marketing's draft MM-170): consequence first, then the state, then the one next step."""
    if ts is None:                      # marketing: no files exist yet, so "may be out of date" would be wrong
        return f"Background sync isn't running, and nothing has synced yet. To start it: {RESTART_COMMAND}"
    return f"Your files may be out of date: background sync isn't running. Last update: {last_update_phrase(ts, now)}. To start it again: {RESTART_COMMAND}"


def newest_success_ts(brain: Brain) -> float | None:
    """MD-212: the newest successful sync across the brain's pipelines (None when nothing synced)."""
    times = [parse_ts(r["ts"]) for r in ActivityLog(brain).read(types={"run"}) if r["result"] in SUCCESS_RESULTS]
    return max(times) if times else None


def brain_status(brain: Brain, pipeline: str | None = None, now: float | None = None) -> dict:
    """Status of every pipeline (or one) plus the brain age. Each pipeline reports its own target (MD-78)."""
    now = now if now is not None else time.time()
    config = load_config(brain)
    runs = ActivityLog(brain).read(types={"run"})
    read: dict[str, tuple[dict, State]] = {}          # one read per target, shared by the pipelines that use it
    out = []
    newest_success: float | None = None
    for p in config.pipelines:
        if pipeline is not None and p.name != pipeline:
            continue
        key = destination_key(brain, p)
        if key not in read:
            read[key] = _target_state(brain, p)
        info, state = read[key]
        target_info = dict(info)
        mine = [r for r in runs if r.get("pipeline") == p.name]
        last_commit = next((r for r in reversed(mine) if r.get("commit")), None)
        if last_commit:
            target_info["last_commit"] = {**last_commit["commit"], "ts": last_commit["ts"], "pipeline": p.name}
        last_clone = next((r for r in reversed(mine) if r.get("clone")), None)
        if last_clone:
            target_info["clone_status"] = {**last_clone["clone"], "ts": last_clone["ts"]}
        meaningful = [r for r in mine if r["result"] not in ("busy", "skipped: paused")]
        last = meaningful[-1] if meaningful else None
        success = next((r for r in reversed(mine) if r["result"] in SUCCESS_RESULTS), None)
        success_ts = parse_ts(success["ts"]) if success else None
        if success_ts is not None and (newest_success is None or success_ts > newest_success):
            newest_success = success_ts
        sources = [source_status(src, mine) for src in p.sources]
        ps = state.pipelines.get(p.name)
        clash = credential_conflict_of(brain, config, p)
        if clash:       # MD-97: an error with its reason known from the config alone (no sync needed), until the conflict is resolved
            status = "error"
        elif p.paused:
            status = "paused"
        elif ps is not None and ps.blocked:
            status = "blocked"
        else:
            status = _status_from_run(last)
        out.append({
            "name": p.name,
            "status": status,
            "schedule": p.schedule,                                     # MD-121: the interval, when it last ran and when it runs next
            "last_sync": last["ts"] if last else None,
            "next_sync": next_sync(p, parse_ts(last["ts"]) if last else None, now),
            "paused": p.paused,
            "target": target_info,
            "last_run": last,
            "last_success": success["ts"] if success else None,
            "last_success_at": iso_utc(success_ts),                       # MD-212: always there, ISO 8601 UTC
            "age_seconds": max(0.0, now - success_ts) if success_ts is not None else None,         # MD-170: never negative after a clock jump
            "conflicts": _with_paths((last or {}).get("conflicts", []), ps) if status in ("conflicts",) else [],
            "guard": (last or {}).get("guard"),
            "pending_approval": ps.pending_approval if ps else None,
            "owned_files": len(ps.files) if ps else 0,
            "sources": sources,
            "warnings": (last or {}).get("warnings", 0),
            "front_matter_warnings": (last or {}).get("front_matter_warnings", []),      # MD-201: from the LAST run only, so an hourly run never piles them up
            "sources_note": f"{sum(1 for x in p.sources if not x.enabled)} of {len(p.sources)} sources off" if any(not x.enabled for x in p.sources) else "",
            "credential_conflict": {"with": [c.name for c in clash], "message": credential_conflict_message(p, clash)} if clash else None,
        })
    result = {"pipelines": out, "brain_age_seconds": max(0.0, now - newest_success) if newest_success is not None else None,
              "last_success_at": iso_utc(newest_success), "stale": False}
    from . import background

    if background.not_running(now):         # MD-212 (AC7): the files may be out of date, and the agent can say so as is
        if newest_success is None:
            message = (f"Markdown Pipeline background sync isn't running, and nothing has synced yet. Ask the user to run `{RESTART_COMMAND}` or open the app.")
        else:       # a human time, in UTC: the agent quotes this as is (the field last_success_at stays ISO 8601)
            when = datetime.fromtimestamp(newest_success, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            message = (f"Markdown Pipeline background sync isn't running, so these files may be out of date. Last update: {when}. "
                       f"Ask the user to run `{RESTART_COMMAND}` or open the app.")
        result.update(stale=True, stale_reason="background_not_running", message_for_user=message)
    return result


def _with_paths(conflicts: list[dict], ps) -> list[dict]:
    """The log names a connector file by its issue key; for the person's own screen the real path is looked up in the state."""
    out = []
    for c in conflicts:
        if "key" in c and ps is not None:
            target = next((t for t, r in ps.files.items() if r.source_label == c.get("source") and r.source_path == c.get("source_path")), None)
            c = {**c, "path": target or c["path"]}
        out.append(c)
    return out


def source_status(source, runs: list[dict]) -> dict:
    """Per source: its type, the last time it was read successfully and the error category of the latest run (None when it was fine).

    Derived from the activity log only: counts and categories, never content."""
    last_fetch, last_category = None, None
    for run in runs:
        if run["result"] in ("busy", "skipped: paused", "blocked", "cancelled") and "sources" not in run:
            continue
        failed = next((e for e in run.get("errors", []) if e.get("source") == source.label and "path" not in e), None)
        if failed is not None:
            last_category = failed.get("category") or "error"
        elif source.label in (run.get("sources") or {}):
            last_fetch, last_category = run["ts"], None
    return {"label": source.label, "type": source.type, "enabled": source.enabled, "last_fetch": last_fetch, "last_error_category": last_category}
