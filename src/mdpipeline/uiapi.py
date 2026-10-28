"""Data views and actions behind the local web UI (pure functions over the brain; no HTTP here)."""
from __future__ import annotations

import json
import os
import time

from . import changes, sample
from .brain import Brain
from .config import Config, Pipeline, load_config
from .engine import sync_all, sync_pipeline
from .locking import is_locked
from .log import ActivityLog
from .snippets import all_snippets
from .state import State, StateError
from .destinations import describe_destination
from .uitargets import github_app_view
from .notices import active_notices
from .status import brain_status, only_missing_sources, parse_ts

PROBLEM_STATUSES = ("conflicts", "blocked", "error")


def count_markdown(directory) -> int:
    total = 0
    for _, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(_, d))]
        total += sum(1 for f in files if f.endswith(".md"))
    return total


def _counts_summary(counts: dict) -> str:
    parts = [f"{counts[k]} {k}" for k in ("created", "updated", "deleted") if counts.get(k)]
    if counts.get("conflicts"):
        parts.append(f"{counts['conflicts']} conflicts")
    return " · ".join(parts) if parts else "no changes"


def _folders_text(pipeline: Pipeline) -> str:
    if len(pipeline.sources) == 1:
        only = pipeline.sources[0]
        return f"Jira {only.label} ({only.options.get('site', '')}): {only.options.get('jql', '')}" if only.type == "jira" else only.path
    if all(s.type == "folder" for s in pipeline.sources):
        return f"{len(pipeline.sources)} folders: " + ", ".join(s.label for s in pipeline.sources)
    kind = {"folder": "folder", "jira": "Jira"}
    return f"{len(pipeline.sources)} sources: " + ", ".join(f"{s.label} ({kind.get(s.type, s.type)})" for s in pipeline.sources)


def _row_summary(status: dict, pipeline: Pipeline) -> str:
    """The one-line summary of a home row; an Off source adds "1 of 2 sources off" (it is information, not a problem)."""
    text = _row_summary_core(status, pipeline)
    off = sum(1 for s in pipeline.sources if not s.enabled)
    return f"{text} · {off} of {len(pipeline.sources)} sources off" if off and status["status"] not in ("error", "blocked") else text


def _row_summary_core(status: dict, pipeline: Pipeline) -> str:
    kind, last = status["status"], status["last_run"]
    clash = status.get("credential_conflict")
    if clash:
        return "uses a different credential than " + ", ".join(f"'{n}'" for n in clash["with"]) + " on the same repository and branch"
    if kind == "never":
        return "never synced"
    if kind == "paused":
        return "paused"
    if kind == "blocked":
        pending = status.get("pending_approval")
        guard = (last or {}).get("guard") or {}
        count, total, pct = (pending["count"], pending["total"], pending["percent"]) if pending else (
            guard.get("count", 0), guard.get("total", 0), guard.get("percent", 0))
        return f"would delete or overwrite {count} of {total} ({pct:g}%)"
    if kind == "error":
        errors = (last or {}).get("errors") or []
        if not errors:
            return (last or {}).get("message") or "error"
        first = errors[0].get("error", "error")
        text = first if len(errors) == 1 else f"{first} (+{len(errors) - 1} more)"
        return f"needs you: {text}" if only_missing_sources(last) else text
    return _counts_summary((last or {}).get("counts", {}))


def clone_needs_you(target: dict | None) -> bool:
    """The local clone could not be fast-forwarded by the last sync that looked at it."""
    return bool(target and (target.get("clone_status") or {}).get("status") == "skipped")


def count_open_items(statuses: list[dict]) -> int:
    """Badge: guard approvals + open conflicts + pipelines in error (PRD section 9, Q9)."""
    total = 0
    for s in statuses:
        if s["status"] == "error":
            total += 1
        elif s["status"] == "blocked" and s.get("pending_approval"):
            total += 1
        total += len(s["conflicts"])
    return total


def _stamp(path) -> str:
    try:
        st = path.stat()
        return f"{st.st_size}.{st.st_mtime_ns}"
    except OSError:
        return "-"


def _ambient(brain: Brain) -> str:
    """What the screens show that does not live in the brain's files: the background settings and the "needs you" notices (not the clocks the
    background process rewrites every minute, which would refresh the page for nothing)."""
    import hashlib

    from .schedulestore import SchedulerStore

    data = SchedulerStore().read()
    notices = (data["runtime"].get(str(brain.root)) or {}).get("notices") or {}
    material = json.dumps([data["background"], sorted((k, v.get("since")) for k, v in notices.items())], sort_keys=True, default=str)
    return hashlib.sha1(material.encode()).hexdigest()[:10]


def _version(brain: Brain) -> str:
    """What the page's 2-second poll compares. Besides the brain's own files (a sync finished, the config changed) it covers what lives outside
    them (MD-162): a "needs you" notice raised or cleared, background on/off, and whether the background process runs, so open screens
    refresh by themselves, with no reload."""
    from . import background

    parts = [_stamp(brain.config_dir / name) for name in ("log.jsonl", "config.yaml", "state.json")]
    parts += [_ambient(brain), "bg" if background.is_running() else "-"]
    return ":".join(parts)


def version_view(brain: Brain) -> dict:
    return {"version": _version(brain), "syncing": is_locked(brain)}


def state_view(brain: Brain, now: float | None = None, running: tuple[str, ...] = ()) -> dict:
    now = now if now is not None else time.time()
    config = load_config(brain)
    state_error = None
    try:
        status = brain_status(brain, now=now)
    except StateError as exc:     # the UI keeps working so the owner can restore from "Needs you"
        state_error = state_error_view(brain, exc)
        status = {"pipelines": [{"name": p.name, "status": "error", "paused": p.paused, "last_run": None, "last_success": None,
                                 "age_seconds": None, "conflicts": [], "guard": None, "pending_approval": None, "owned_files": 0,
                                 "target": describe_destination(brain, p)}
                                for p in config.pipelines], "brain_age_seconds": None}
    by_name = {s["name"]: s for s in status["pipelines"]}
    rows = []
    for p in config.pipelines:
        s = by_name[p.name]
        last = s["last_run"]
        rows.append({
            "name": p.name,
            "sources": [src.public_dict() for src in p.sources],
            "folders_text": _folders_text(p),
            "target": p.target,
            "destination": s["target"],
            "on_source_delete": p.on_source_delete,
            "status": s["status"],
            "needs_you": s["status"] == "error" and only_missing_sources(last) and not s.get("credential_conflict"),     # MD-136 AC3: amber, not red
            "paused": p.paused,
            "links_to_activity": s["status"] in PROBLEM_STATUSES,
            "last_sync_ts": s["last_success"] if s["last_success"] else (last["ts"] if last else None),
            "age_seconds": s["age_seconds"],
            "last_run": {"ts": last["ts"], "trigger": last["trigger"], "result": last["result"], "counts": last["counts"]} if last else None,
            "summary": _row_summary(s, p),
            "owned_files": s["owned_files"],
            "pending_approval": bool(s.get("pending_approval")),
            "syncing": p.name in running,
            "schedule": p.schedule,                                    # MD-121: the interval and when it last / next runs by itself
            "last_sync": s.get("last_sync"),
            "next_sync": s.get("next_sync"),
            "needs_signin": bool(last and last.get("category") == "sign-in-expired" and (p.destination or {}).get("auth") == "sign-in"),   # MD-137
            "sample": sample.is_sample(p),                             # MD-124: the bundled sample is labelled on every screen
            "last_changes": changes.row_view(brain, p.name),           # MD-122: "3 new · 5 changed · 1 deleted" and the items behind it
        })
    raw_md = count_markdown(brain.raw_dir)
    from .secretimport import pending_imports

    badge = 1 if state_error else (count_open_items(status["pipelines"]) + sum(1 for s in status["pipelines"] if clone_needs_you(s["target"]))
                                   + (1 if pending_imports(brain) else 0))
    if state_error:
        for r in rows:
            r["summary"] = "state unreadable: restore from Needs you"
    if not rows:
        summary = (f"{raw_md} Markdown file{'s' if raw_md != 1 else ''} already in raw/ · " if raw_md else "") + "no pipelines yet"
    else:
        last_runs = [r for r in rows if r["last_run"]]
        newest = max(last_runs, key=lambda r: r["last_run"]["ts"]) if last_runs else None
        summary = (f"Last sync: {newest['name']} {newest['last_run']['result']}" if newest else "Never synced") + \
                  (f" · {badge} item{'s' if badge != 1 else ''} need{'' if badge != 1 else 's'} you" if badge else "")
    return {"brain": {"path": str(brain.root), "raw_md_files": raw_md}, "pipelines": rows, "badge": badge,
            "summary": summary, "github_app": github_app_view(), "usage": usage_summary(), "syncing": is_locked(brain), "running": list(running), "state_error": state_error,
            "connect": all_snippets(brain), "brain_age_seconds": status["brain_age_seconds"], **version_view(brain),
            "notices": active_notices(brain), "background": background_view()}


# --- scheduled sync (MD-121) -------------------------------------------------------------------
def background_view() -> dict:
    """The background process as Settings and Home show it: the person's choice, whether it runs, and whether the plain-words announcement
    is still waiting to be acknowledged (it stays on screen across reloads until OK, Change or Turn off)."""
    from . import autostart, background
    from .scheduler import ANNOUNCEMENT
    from .schedulestore import SchedulerStore

    state = SchedulerStore().read()["background"]
    pending = bool(state.get("announced")) and not state.get("acknowledged")
    view = {"enabled": bool(state.get("enabled", True)), "announced": bool(state.get("announced")), "running": background.is_running(),
            "announce_pending": pending}
    view["linger_hint"] = view["enabled"] and autostart.linger_hint()
    from .scheduler import linger_notice_hidden

    view["linger_notice"] = bool(view["enabled"] and not linger_notice_hidden() and autostart.linger_notice())
    if view["linger_hint"]:
        view["linger_text"] = autostart.LINGER_LINE
    if pending:
        view["announcement"] = ANNOUNCEMENT
    return view


def set_schedule_view(brain: Brain, name: str, interval: object) -> dict:
    from .service import set_schedule

    changed = set_schedule(brain, name, str(interval), trigger="ui")      # raises ValueError for an unknown interval
    return {"name": name, "schedule": str(interval), "changed": changed}


def set_background_view(enabled: object) -> dict:
    from . import scheduler

    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if enabled:
        scheduler.turn_on_background()
    else:
        scheduler.turn_off_background()
    return background_view()


def _with_announcement(view: dict, results) -> dict:
    """The first successful sync turns the background process on; the page says so in plain words, once."""
    from .scheduler import ensure_background

    if any(r.result in ("ok", "conflicts") and not r.dry_run for r in results):
        text = ensure_background()
        if text:
            view = {**view, "announcement": text}
    return view


# --- usage data (MD-115) -----------------------------------------------------------------------
def usage_summary() -> dict:
    """What the home page needs to know to show or hide the consent card."""
    from .usage import UsageStore

    store = UsageStore()
    return {"consent": store.consent, "sharing": store.sharing}


def usage_view() -> dict:
    from .usage import UsageStore

    return UsageStore().view()


def set_usage_consent(choice: object) -> dict:
    """The consent card / Settings toggle. ``no_answer`` = the user carried on without choosing (still not sharing)."""
    from . import usage

    store = usage.UsageStore()
    if choice == "unanswered":        # "Undo" on the card: back to not chosen. Not a decision, so nothing is counted.
        store.set_consent("unanswered")
        return usage_view()
    try:
        store.decide(str(choice), usage.get_transport())
    except ValueError as exc:
        raise ValueError("choice must be share, decline, no_answer or unanswered") from exc
    return usage_view()


def usage_event(body: object) -> dict:
    """An event the page reports (view mode opened, snippet copied...). Only events the UI owns; the allowlist cleans the rest."""
    from . import usage
    from .usage_events import EVENTS

    data = body if isinstance(body, dict) else {}
    name, props = data.get("event"), data.get("properties")
    event = EVENTS.get(name) if isinstance(name, str) else None
    if event is None or "ui" not in event.surfaces:
        return {"queued": False}
    props = props if isinstance(props, dict) else {}
    if name == "notice_shown" and props.get("kind") == "linger":      # MD-168: once per screen per day, whatever the page redraws
        from . import usage_hooks

        return {"queued": usage_hooks.linger_notice_shown(str(props.get("surface", "")))}
    return {"queued": usage.emit(name, **props)}


def reset_usage_install_id() -> dict:
    from .usage import UsageStore

    store = UsageStore()
    store.reset_install_id()
    return store.view()


# --- actions ---------------------------------------------------------------------------------
def run_sync(brain: Brain, name: str) -> dict:
    res = sync_pipeline(brain, name, trigger="ui")
    return _with_announcement(res.to_dict(), [res])


def run_sync_all(brain: Brain) -> dict:
    results = sync_all(brain, trigger="ui")
    return _with_announcement({"results": [r.to_dict() for r in results]}, results)


def set_paused_view(brain: Brain, name: str, paused: bool) -> dict:
    from .service import set_paused

    changed = set_paused(brain, name, paused, trigger="ui")
    return {"name": name, "paused": paused, "changed": changed}


def os_open(path: str) -> None:
    """Open ``path`` with the operating system's default application (no shell)."""
    import subprocess
    import sys

    if sys.platform == "win32":                    # MD-182 AC1: the Windows default app (a file or a folder); OSError when it cannot
        import os

        os.startfile(path)                         # noqa: S606 - the path is a file or folder the app itself resolved, never a command
        return
    command = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def open_file(brain: Brain, path: str, opener=None) -> dict:
    """Open a Markdown file of raw/ or wiki/ in the OS default app. Returns ok=False (never raises) if that fails."""
    from .brainquery import QueryError, _resolve

    try:
        absolute = str(_resolve(brain, path, "file"))
    except QueryError as exc:
        raise ValueError(str(exc)) from exc
    try:
        (opener or os_open)(absolute)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "absolute_path": absolute}
    return {"ok": True, "absolute_path": absolute}


def state_error_view(brain: Brain, exc: Exception) -> dict:
    from .recovery import usable_generations

    return {"message": str(exc), "backups": len(usable_generations(brain))}


def restore_state(brain: Brain, apply: bool, generation: int | None = None) -> dict:
    """The restore preview (nothing written) or, with ``apply``, the restore itself."""
    from .recovery import apply_restore, preview_restore

    result = apply_restore(brain, generation, trigger="ui") if apply else preview_restore(brain, generation)
    return result.to_dict()


def run_approve(brain: Brain, name: str) -> dict:
    return sync_pipeline(brain, name, trigger="ui", approve=True).to_dict()


def run_cancel(brain: Brain, name: str) -> dict:
    from .engine import cancel_pending

    return cancel_pending(brain, name, trigger="ui").to_dict()


def run_force(brain: Brain, name: str, paths: list[str]) -> dict:
    return sync_pipeline(brain, name, trigger="ui", force_paths=frozenset(paths)).to_dict()
