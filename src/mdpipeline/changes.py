"""What changed in a sync (MD-122, FR-S8): "3 new · 5 changed · 1 deleted", read from the local activity log. Pure reads, no network.

Used by Home and view mode (the last sync, with its item list) and by the MCP ``recent_changes`` tool (a time window).
Titles are for the UI and the local agent only; nothing here reaches the usage data.
"""
from __future__ import annotations

import posixpath
from datetime import datetime, timedelta, timezone

from .brain import Brain
from .log import ActivityLog

SUCCESS_RESULTS = ("ok", "conflicts")
OPS = {"create": "new", "update": "changed", "rename": "changed", "delete": "deleted"}
ORDER = ("new", "changed", "deleted")
DEFAULT_WINDOW = timedelta(hours=24)
DEFAULT_LIMIT = 200


def _text(counts: dict) -> str:
    parts = [f"{counts[k]} {k}" for k in ORDER if counts.get(k)]
    return " · ".join(parts) if parts else "no changes"


def _title(f: dict) -> str:
    if f.get("key") and not f.get("target"):                    # a connector's file is logged by key, never by title (MD-71)
        return f["key"]
    return posixpath.splitext(posixpath.basename(f.get("target", "")))[0]


def _items(run: dict) -> list[dict]:
    out = []
    for f in run.get("files", []):
        op = OPS.get(f.get("action", ""))
        if op is None:
            continue
        path = f.get("target") or f.get("key", "")
        out.append({"op": op, "path": path, "title": _title(f), "pipeline": run.get("pipeline", "")})
    return out


def _tally(items: list[dict]) -> dict:
    return {k: sum(1 for i in items if i["op"] == k) for k in ORDER}


def _run_counts(run: dict) -> dict:
    c = run.get("counts") or {}
    return {"new": c.get("created", 0), "changed": c.get("updated", 0) + c.get("renamed", 0), "deleted": c.get("deleted", 0)}


def last_sync(brain: Brain, pipeline: str) -> dict | None:
    """The pipeline's last successful sync: time, counts, readable text and the item list. None if it never synced."""
    runs = [r for r in ActivityLog(brain).read(pipeline=pipeline, types={"run"}) if not r.get("dry_run") and r.get("result") in SUCCESS_RESULTS]
    if not runs:
        return None
    run = runs[-1]
    counts = _run_counts(run)
    return {"ts": run["ts"], "trigger": run.get("trigger", ""), "counts": counts, "text": _text(counts), "items": _items(run)}


ROW_ITEMS = 50


def row_view(brain: Brain, pipeline: str) -> dict | None:
    """``last_sync`` shaped for a screen: the item list is capped and ``more`` says how many were left out."""
    last = last_sync(brain, pipeline)
    if last is None:
        return None
    items = last["items"]
    return {**last, "items": items[:ROW_ITEMS], "more": max(len(items) - ROW_ITEMS, 0)}


def _parse(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a time: use an ISO date or time such as 2026-10-11 or 2026-10-11T08:00:00Z") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _merge(moments: list[tuple[datetime, dict]]) -> list[dict]:
    """One row per file across the window: new then edited stays new, new then deleted vanishes, edited then deleted is deleted."""
    net: dict[tuple[str, str], dict] = {}
    for _, item in moments:
        key = (item["pipeline"], item["path"])
        before = net.get(key)
        if before is None:
            net[key] = dict(item)
        elif item["op"] == "deleted":
            if before["op"] == "new":
                del net[key]
            else:
                net[key] = dict(item)
        elif before["op"] == "deleted":
            net[key] = {**item, "op": "changed"}                   # deleted, then back
        elif before["op"] == "new":
            net[key] = {**before, "title": item["title"]}
        else:
            net[key] = dict(item)
    return list(net.values())


def recent(brain: Brain, since: str | None = None, pipeline: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    """Every file that changed in the successful syncs since ``since`` (default: the last 24 hours), merged per file. Raises ValueError on a bad time."""
    start = _parse(since) if since else datetime.now(timezone.utc) - DEFAULT_WINDOW
    moments = []
    syncs = 0
    for run in ActivityLog(brain).read(pipeline=pipeline, types={"run"}):
        if run.get("dry_run") or run.get("result") not in SUCCESS_RESULTS:
            continue
        when = _parse(run["ts"])
        if when < start:
            continue
        syncs += 1
        moments += [(when, i) for i in _items(run)]
    merged = _merge(moments)
    capped = merged[:max(limit, 0)]
    return {"since": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "syncs": syncs, "counts": _tally(merged), "text": _text(_tally(merged)),
            "items": capped, "truncated": len(merged) > len(capped)}
