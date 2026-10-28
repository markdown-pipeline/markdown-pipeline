"""Turns what the app does into content-free usage events (MD-116). The only place that decides what an event says.

Everything here reduces to counts, buckets and fixed enums before it reaches ``usage.emit``, and ``emit`` checks the allowlist again.
Every function is safe to call from any code path: it never raises and never blocks (sending happens in the background).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import usage
from .config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE

COUNTED = ("created", "updated", "renamed")          # what a sync changed
FILES = ("created", "updated", "renamed", "unchanged")   # the files a sync is responsible for


def bucket(n: int) -> str:
    """A count as one of the fixed buckets (0, 1-10, 11-100, 101-1000, 1000+)."""
    if n <= 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 100:
        return "11-100"
    if n <= 1000:
        return "101-1000"
    return "1000+"


def trigger_class(trigger: str) -> str:
    """The scheduler is ``schedule``; the CLI, the UI and an agent (MCP) are all someone asking: ``manual``."""
    return "schedule" if trigger == "schedule" else "manual"


def source_type_of(pipeline) -> str:
    """folder, jira, or other for a pipeline that mixes source types."""
    kinds = {"jira" if s.type == "jira" else "folder" if s.type == "folder" else "other" for s in pipeline.sources}
    return kinds.pop() if len(kinds) == 1 else "other"


_CLASS_OF_CATEGORY = {"auth": "auth", "vault-locked": "auth", "vault-unsafe": "auth", "rate-limited": "rate_limit", "network": "network",
                      "protected-branch": "target_write", "head-moved": "target_write", "concurrent-change": "target_write",
                      "credential-conflict": "validation",
                      "probe-rejected": "probe_rejected"}          # MD-150: a question WE asked was refused; never what the person typed


def error_class_of(category: str) -> str:
    """A failure category (our own fixed names) as one of the fixed error classes. Anything unknown is ``unknown``: never the message."""
    category = category or ""
    if category.startswith("bad-"):                      # bad-query, bad-jira-site, bad-jira-credential: what the user typed was refused
        return "validation"
    return _CLASS_OF_CATEGORY.get(category, "unknown")


def _error_class_of_result(res) -> str:
    categories = [res.category] + [g.category for g in (res.plan.groups if res.plan else []) if getattr(g, "category", "")]
    for category in categories:
        cls = error_class_of(category)
        if cls != "unknown":
            return cls
    return "unknown"


def _locked(res) -> str | None:
    """The notice kind when the run stopped only because the vault was locked (``vault_locked``) or its file unsafe (``vault_unsafe``): on the
    result itself, or on every source group that failed. ``None`` for any other run."""
    failed = [g for g in (res.plan.groups if res.plan else []) if g.status not in ("ok", "off")]
    categories = {getattr(g, "category", "") for g in failed} if failed else {res.category}
    if categories == {"vault-locked"}:
        return "vault_locked"
    return "vault_unsafe" if categories <= {"vault-locked", "vault-unsafe"} and "vault-unsafe" in categories else None


# --- the sync engine ---------------------------------------------------------------------------------------------------
def sync_finished(res, pipeline) -> None:
    """Called once for every recorded run (never for a dry run). Reports completion, failure, the first file, and the guard."""
    try:
        if res.dry_run:
            return
        kind, counts = source_type_of(pipeline), res.counts()
        seconds = round(res.duration_ms / 1000, 1)
        if res.result in ("ok", "conflicts"):
            usage.emit("sync_completed", trigger=trigger_class(res.trigger), source_type=kind,
                       changed_bucket=bucket(sum(counts.get(k, 0) for k in COUNTED)), deleted_bucket=bucket(counts.get("deleted", 0)),
                       duration_s=seconds)
            files = sum(counts.get(k, 0) for k in FILES)
            store = usage.UsageStore()
            if files > 0 and store.note_first_sync():          # the clock is local and runs even without consent
                usage.emit("first_sync_completed", source_type=kind, files_bucket=bucket(files), duration_s=seconds,
                           ms_since_first_open=store.ms_since_first_open())
            if getattr(res, "approved", False):
                usage.emit("guard_resolved", cause="unknown", action="approved")
        elif res.result == "error" and _locked(res):
            usage.emit("notice_shown", kind=_locked(res))      # MD-161: waiting for the person to unlock is not a failure; the reliability numbers stay clean
        elif res.result == "error":
            usage.emit("sync_failed", error_class=_error_class_of_result(res), source_type=kind)
        elif res.result == "blocked":
            usage.emit("guard_triggered", cause="unknown")
            usage.emit("sync_failed", error_class="guard_blocked", source_type=kind)
    except Exception:                   # noqa: BLE001 - reporting must never change or fail a sync
        return


def guard_cancelled() -> None:
    try:
        usage.emit("guard_resolved", cause="unknown", action="cancelled")
    except Exception:                   # noqa: BLE001
        return


# --- JQL: only field names, a clause count and whether it sorts ----------------------------------------------------------
_FIELD_ALIASES = {"issuetype": "issuetype", "type": "issuetype", "issue type": "issuetype", "status": "status", "priority": "priority",
                  "updated": "updated", "updateddate": "updated", "created": "created", "createddate": "created", "labels": "labels",
                  "label": "labels", "component": "component", "assignee": "assignee", "project": "project", "sprint": "sprint"}
_TOKEN = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|!=|!~|>=|<=|=|~|>|<|[A-Za-z_][\w.]*|\(|\)|,")
_SYMBOL_OPS = {"=", "!=", "~", "!~", ">", "<", ">=", "<="}
_WORD_OPS = {"in", "not", "is", "was", "changed"}
_KEYWORDS = {"and", "or", "not", "order", "by", "asc", "desc"}


def jql_profile(jql: str) -> dict:
    """Which standard fields a JQL uses, how many clauses it has and whether it has ORDER BY. No value, project key or text survives."""
    text = jql or ""
    uses_order_by = re.search(r"\border\s+by\b", text, re.I) is not None
    main = re.split(r"\border\s+by\b", text, maxsplit=1, flags=re.I)[0]
    tokens = _TOKEN.findall(main)
    fields: set[str] = set()
    clauses = 0
    for i, token in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        is_field = token[0] in "\"'" or (re.match(r"[A-Za-z_]", token) and token.lower() not in _KEYWORDS and token.lower() not in _WORD_OPS)
        if is_field and (nxt in _SYMBOL_OPS or nxt.lower() in _WORD_OPS):
            clauses += 1
            name = token.strip("\"'").strip().lower()
            fields.add(_FIELD_ALIASES.get(name, "custom"))
    return {"fields_used": sorted(fields), "clause_count": clauses, "uses_order_by": uses_order_by}


# --- configuration: pipelines, sources, target, scope -----------------------------------------------------------------------
def _kind_of(source) -> str:
    return "jira" if source.type == "jira" else "folder" if source.type == "folder" else "other"


def target_type_of(pipeline) -> str:
    """folder (a directory) or github."""
    return "github" if (pipeline.destination or {}).get("type") == "github" else "folder"


def _destination_key(pipeline) -> tuple:
    d = pipeline.destination or {}
    return (target_type_of(pipeline), d.get("repo", ""), d.get("branch", ""), d.get("path", "") if target_type_of(pipeline) == "folder" else "")


def _emit_jql_scope(source) -> None:
    jql = (source.options or {}).get("jql", "")
    if source.type == "jira" and jql.strip():
        usage.emit("source_scope_edited", source_type="jira", kind="jql", **jql_profile(jql))


def _emit_glob_scope(pipeline) -> None:
    if list(pipeline.include) != list(DEFAULT_INCLUDE) or list(pipeline.exclude) != list(DEFAULT_EXCLUDE):
        usage.emit("source_scope_edited", source_type=source_type_of(pipeline), kind="glob",
                   pattern_count=len(pipeline.include) + len(pipeline.exclude))


def _source_added(source, total: int) -> None:
    usage.emit("source_added", source_type=_kind_of(source), n_sources=total)
    usage.emit("source_connected", source_type=_kind_of(source))
    _emit_jql_scope(source)


def pipeline_created(n_pipelines: int, pipeline) -> None:
    """A pipeline was saved (CLI or UI): the pipeline, each of its sources, its target and any narrowed scope."""
    try:
        usage.emit("pipeline_created", n_pipelines=n_pipelines)
        for source in pipeline.sources:
            _source_added(source, len(pipeline.sources))
        usage.emit("target_connected", target_type=target_type_of(pipeline))
        _emit_glob_scope(pipeline)
    except Exception:                   # noqa: BLE001
        return


def pipeline_removed(pipeline) -> None:
    """A whole pipeline was removed in the UI (MD-134): the existing ``source_removed`` event, once per source. Never which one."""
    try:
        for _ in pipeline.sources:
            usage.emit("source_removed")
    except Exception:                   # noqa: BLE001
        return


def pipeline_updated(old, new) -> None:
    """A pipeline was edited: what was added, removed, switched on/off, re-scoped or re-targeted. Nothing for an edit that changes none of these."""
    try:
        before = {s.label: s for s in old.sources}
        for source in new.sources:
            was = before.get(source.label)
            if was is None:
                _source_added(source, len(new.sources))
                continue
            if was.enabled != source.enabled:
                usage.emit("source_toggled", state="on" if source.enabled else "off")
            if source.type == "jira" and (was.options or {}).get("jql", "") != (source.options or {}).get("jql", ""):
                _emit_jql_scope(source)
        for label in before:
            if label not in {s.label for s in new.sources}:
                usage.emit("source_removed")                 # which one, and for how long, is not recorded
        if _destination_key(old) != _destination_key(new):
            usage.emit("target_connected", target_type=target_type_of(new))
        if (list(old.include), list(old.exclude)) != (list(new.include), list(new.exclude)):
            _emit_glob_scope(new)
        if old.schedule != new.schedule:
            schedule_changed(old.schedule, new.schedule)
    except Exception:                   # noqa: BLE001
        return


def schedule_changed(old_interval: str, new_interval: str) -> None:
    """A pipeline's scheduled-sync interval changed (MD-121). Only the two fixed interval buckets, never the pipeline."""
    try:
        usage.emit("schedule_changed", old_interval=old_interval, new_interval=new_interval)
    except Exception:                   # noqa: BLE001
        return


def background_started(how: str) -> None:
    """The background process holds its lock and starts ticking (MD-165). How it was started, and (MD-168) whether a desktop session exists and
    whether lingering is on: fixed yes/no/unknown words, read only."""
    try:
        from . import autostart

        usage.emit("background_started", how=how, desktop_session=autostart.desktop_state(), linger_enabled=autostart.linger_state())
    except Exception:                   # noqa: BLE001
        return


def linger_notice_shown(surface: str, now: datetime | None = None) -> bool:
    """MD-168 (H-LINGER-1): the linger line was on screen at ``surface`` (ready / home / settings / cli). Reported once per screen per UTC day,
    however often the screen redraws. Returns whether it was reported."""
    try:
        day = (now or datetime.now(timezone.utc)).date().isoformat()
        if not usage.UsageStore().claim_daily(f"linger_notice_{surface}", day):
            return False
        return usage.emit("notice_shown", kind="linger", surface=surface)
    except Exception:                   # noqa: BLE001
        return False


def linger_notice_hidden(surface: str = "home") -> None:
    """MD-168: the person pressed Hide on the linger line. A fixed kind and the screen."""
    try:
        usage.emit("notice_hidden", kind="linger", surface=surface)
    except Exception:                   # noqa: BLE001
        return


# --- opening the app, the credential check ------------------------------------------------------------------------------------
def app_opened(*, first_only: bool = False) -> None:
    """The app was opened (the UI server served its page, or a CLI run). Starts the time-to-value clock the first time, even without
    consent; also reports an update once, with the previous version only. ``first_only``: a CLI run is not an "open" unless it is the
    very first use (the UI reports every launch)."""
    try:
        store = usage.UsageStore()
        first = store.touch_first_open()
        previous = store.swap_version(usage.__version__)
        if first or not first_only:
            usage.emit("app_opened", first_open=first)
        if previous:
            _refresh_login_item()               # MD-171: after an update, an old login item is rewritten (with or without usage consent)
            usage.emit("app_updated", previous_version=previous)
    except Exception:                   # noqa: BLE001
        return


def _refresh_login_item() -> None:
    try:
        from . import scheduler

        scheduler.refresh_login_item()
    except Exception:                   # noqa: BLE001 - housekeeping must never break opening the app
        return


def jira_checked(ok: bool, category: str = "") -> None:
    """The user tested a Jira connection (UI or CLI): started, then completed or failed (a class, never the message)."""
    try:
        usage.emit("source_auth_started", source_type="jira", auth_method="token")
        if ok:
            usage.emit("source_auth_completed", source_type="jira", auth_method="token")
        else:
            usage.emit("source_auth_failed", source_type="jira", auth_method="token", error_class=error_class_of(category))
    except Exception:                   # noqa: BLE001
        return


def jira_project_picked(method: str, count: int) -> None:
    """The person chose Jira projects with the picker, or wrote their own JQL (MD-141): a method and a count bucket, nothing else."""
    try:
        usage.emit("jira_project_pick", method=method, count_bucket=bucket(count))
    except Exception:                   # noqa: BLE001
        return


def dry_run_viewed(res) -> None:
    """A dry run was shown (``sync --dry-run``): how many items and how many would be deleted, as buckets."""
    try:
        if not (res.dry_run and res.plan):
            return
        counts = res.counts()
        items = sum(n for key, n in counts.items() if key not in ("errors", "skipped"))
        usage.emit("dry_run_viewed", items_bucket=bucket(items), would_delete_bucket=bucket(counts.get("deleted", 0)))
    except Exception:                   # noqa: BLE001
        return
