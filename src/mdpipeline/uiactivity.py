"""The Activity screen: 'Needs you' (errors, guard approvals, conflicts) and the run history."""
from __future__ import annotations

from .brain import Brain
from .config import load_config
from .engine import sync_pipeline
from .log import ActivityLog
from .status import brain_status, only_missing_sources
from .state import StateError
from .uiapi import clone_needs_you, count_open_items, state_error_view
from .targetmessages import CLONE_NEXT_STEP, NEXT_STEPS

HISTORY_LIMIT = 200


def _counts_text(counts: dict) -> str:
    parts = [f"{counts[k]} {k}" for k in ("created", "updated", "deleted") if counts.get(k)]
    return ", ".join(parts) if parts else "no changes"


def _missing_source_cards(brain: Brain, s: dict, config) -> list[dict]:
    """MD-136: one calm card per local folder that was not found. Nothing is at risk (files are never removed for it), so it is not an error."""
    from .state import State
    from .destinations import bind_target

    last = s["last_run"]
    pipeline = config.get(s["name"])
    kinds = {src.label: src for src in pipeline.sources} if pipeline else {}
    wanted = [e for e in last.get("errors", [])
              if "path" not in e and kinds.get(e["source"]) is not None and kinds[e["source"]].type == "folder" and e.get("category") == "source-missing"]
    if not wanted:
        return []
    ps = State.load(bind_target(brain, pipeline)).pipelines.get(s["name"])
    owned: dict[str, int] = {}
    for rec in (ps.files.values() if ps else []):
        owned[rec.source_label] = owned.get(rec.source_label, 0) + 1
    return [{"kind": "source_missing", "pipeline": s["name"], "label": e["source"], "path": kinds[e["source"]].path,
             "kept_files": owned.get(e["source"], 0), "trigger": last["trigger"], "ts": last["ts"]} for e in wanted]


FILE_ERRORS_NEXT_STEP = ("Fix them at the source and press Sync now to retry. If the same files fail again, "
                         "report them with the issue keys shown above (no content is sent).")


def _error_cards(brain: Brain, s: dict, config) -> list[dict]:
    """The calm missing-folder cards, plus the red error card only when something else really failed."""
    cards = _missing_source_cards(brain, s, config)
    missing = {c["label"] for c in cards}
    last = s["last_run"]
    others = [e for e in last.get("errors", []) if "path" in e or e["source"] not in missing]
    if cards and not others:
        return cards
    return cards + [_error_card(brain, s, config, skip=missing)]


def _error_card(brain: Brain, s: dict, config, skip: frozenset | set = frozenset()) -> dict:
    last = s["last_run"]
    last = {**last, "errors": [e for e in last.get("errors", []) if "path" in e or e["source"] not in skip]}
    pipeline = config.get(s["name"])
    paths = {src.label: src.path for src in pipeline.sources} if pipeline else {}
    kinds = {src.label: src for src in pipeline.sources} if pipeline else {}
    folders = [{"label": e["source"], "path": paths.get(e["source"], ""), "error": e["error"]}
               for e in last.get("errors", []) if "path" not in e and kinds.get(e["source"], None) is not None and kinds[e["source"]].type == "folder"
               or ("path" not in e and e["source"] not in kinds)]
    connectors = [_connector_error(kinds[e["source"]], e) for e in last.get("errors", [])
                  if "path" not in e and e["source"] in kinds and kinds[e["source"]].type != "folder"]
    file_errors = [{"source": e["source"], "path": e["path"], "error": e["error"]} for e in last.get("errors", []) if "path" in e]
    total = len(pipeline.sources) if pipeline else len(folders)
    bad = {f["label"] for f in folders} | {c["label"] for c in connectors}
    other_labels = [src.label for src in (pipeline.sources if pipeline else []) if src.label not in bad]
    if connectors and not folders:
        title = f"{connectors[0]['kind_title']}" if len(connectors) == 1 else f"{len(connectors)} sources failed"
    elif folders and len(folders) < total:
        title = f"{len(folders)} of {total} folders missing"
    elif folders or connectors:
        title = "sync failed"
    else:
        title = f"{len(file_errors)} file{'s' if len(file_errors) != 1 else ''} could not be copied"
    kept = sum(1 for f in last.get("files", []) if f["action"] == "keep" and "nothing deleted" in f.get("reason", ""))
    synced = {"created": 0, "updated": 0, "deleted": 0}
    for label in other_labels:
        for k in synced:
            synced[k] += (last.get("sources", {}).get(label) or {}).get(k, 0)
    written = sum((v or {}).get(k, 0) for v in last.get("sources", {}).values() for k in ("created", "updated"))
    return {"kind": "error", "pipeline": s["name"], "title": title, "trigger": last["trigger"], "ts": last["ts"],
            "files_written": written, "file_errors_next_step": FILE_ERRORS_NEXT_STEP,
            "folders": folders, "connectors": connectors, "file_errors": file_errors, "kept_files": kept, "other_labels": other_labels,
            "other_synced": _counts_text(synced), "total_folders": total}


def _target_card(s: dict) -> dict:
    last = s["last_run"]
    category = last.get("category") or "error"
    title, next_step = NEXT_STEPS.get(category, NEXT_STEPS["error"])
    return {"kind": "target", "pipeline": s["name"], "category": category, "title": title, "message": last.get("message", ""),
            "next_step": next_step, "trigger": last["trigger"], "ts": last["ts"]}


def _conflict_target_card(s: dict) -> dict:
    """MD-97: the pipeline would write with a credential another pipeline's setup did not choose; found from the config, no sync needed."""
    title, next_step = NEXT_STEPS["credential-conflict"]
    return {"kind": "target", "pipeline": s["name"], "category": "credential-conflict", "title": title, "message": s["credential_conflict"]["message"],
            "next_step": next_step, "trigger": "config", "ts": ""}


def _clone_card(target: dict, pipeline: str = "") -> dict:
    clone = target["clone_status"]
    return {"kind": "clone", "pipeline": pipeline, "title": "The local clone was not updated", "reason": clone.get("reason", ""), "path": target.get("clone", ""),
            "next_step": CLONE_NEXT_STEP, "ts": clone.get("ts", "")}


JIRA_NEXT_STEPS = {
    "auth": "Token rejected: create a new API token at id.atlassian.com (Security, API tokens) and update the variable or keychain entry {ref}, then Sync now.",
    "permission": "The account may not read these issues: check its permission for the project in Jira, then Sync now.",
    "rate-limited": "Jira's request limit was reached. Nothing was changed; wait a while, then Sync now.",
    "network": "Jira could not be reached. Nothing was changed; check the network and try again.",
    "site-not-found": "Check the site URL in Edit (https://yourcompany.atlassian.net), then Sync now.",
    "bad-query": "Jira refused the JQL. Fix it in Edit (use Test connection to see Jira's message), then Sync now.",
    "malformed": "Jira answered something unexpected. Nothing was changed; try again later.",
}


def _connector_error(source, e: dict) -> dict:
    category = e.get("category") or "error"
    ref = source.options.get("token_ref", "the token reference")
    step = JIRA_NEXT_STEPS.get(category, "Nothing was changed. Use Test connection in Edit to see what is wrong, then Sync now.")
    if category == "auth" and ref.startswith("vault:"):
        step = "Token rejected: create a new API token at id.atlassian.com (Security, API tokens), enter it as the new API token in Edit, then Sync now."
    step = step.replace("{ref}", ref)
    titles = {"auth": "Jira rejected the token", "permission": "No permission in Jira", "rate-limited": "Jira rate limit reached",
              "network": "Jira could not be reached", "site-not-found": "Jira site not found", "bad-query": "Jira refused the JQL"}
    return {"label": source.label, "type": source.type, "site": source.options.get("site", source.path), "category": category,
            "error": e["error"], "next_step": step, "kind_title": titles.get(category, "Jira source failed")}


def _guard_card(brain: Brain, s: dict, runs: list[dict]) -> dict:
    name = s["name"]
    res = sync_pipeline(brain, name, trigger="ui", dry_run=True)
    plan = res.plan
    items = plan.items() if plan else []
    guard = plan.guard.to_dict() if plan else (s["guard"] or {})
    blocked_run = next((r for r in reversed(runs) if r.get("pipeline") == name and r["result"] == "blocked"), None)
    return {"kind": "guard", "pipeline": name, "count": guard.get("count", 0), "total": guard.get("total", 0),
            "percent": guard.get("percent", 0), "threshold": guard.get("threshold", 0), "min_files": guard.get("min_files", 0),
            "message": guard.get("message", ""),
            "will_delete": [i.target for i in items if i.action == "delete"],
            "will_update": [i.target for i in items if i.action == "update" and not i.metadata_only],
            "metadata_only": [i.target for i in items if i.action == "update" and i.metadata_only],
            "will_create": [i.target for i in items if i.action == "create"],
            "will_rename": [{"from": i.from_target, "to": i.target} for i in items if i.action == "rename"],
            "trigger": blocked_run["trigger"] if blocked_run else "", "ts": blocked_run["ts"] if blocked_run else "",
            "cli_hint": f"mdpipeline sync {name} --approve"}


def _local_paths(brain: Brain, pipeline, rel: str) -> tuple[str | None, str | None]:
    """Where a file of this pipeline is on this machine (None: not at all, e.g. a GitHub target without a clone), and the
    path the Open action takes (``@id/...`` when the pipeline's files are not in the brain's own folder)."""
    from .brainquery import _roots
    from .destinations import local_root

    root = local_root(brain, pipeline)
    if root is None:
        return None, None
    absolute = str(root / rel)
    if root.resolve() == brain.root.resolve():
        return absolute, rel
    prefix = next((pre for pre, (folder, _) in _roots(brain).items() if folder.resolve() == root.resolve()), None)
    return absolute, (f"{prefix}/{rel}" if prefix else None)


def _conflict_card(s: dict, brain: Brain | None = None, pipeline=None) -> dict:
    last = s["last_run"]
    detail = {(f.get("target") or f.get("key")): f for f in last.get("files", []) if f["action"] == "conflict"}
    items = []
    for c in s["conflicts"]:
        abs_path, open_path = _local_paths(brain, pipeline, c["path"]) if brain and pipeline else (None, c["path"])
        items.append({"type": c["type"], "path": c["path"], "reason": detail.get(c.get("key") or c["path"], {}).get("reason", ""),
                      "source_path": detail.get(c.get("key") or c["path"], {}).get("source_path", ""),
                      "abs_path": abs_path, "open_path": open_path,
                      **({"next_step": c["next_step"]} if c.get("next_step") else {})})
    return {"kind": "conflicts", "pipeline": s["name"], "items": items, "trigger": last["trigger"], "ts": last["ts"]}


def _event_text(e: dict) -> str:
    action = e.get("action")
    return {
        "pause": "Paused. Skipped by every trigger (UI, CLI, MCP) until resumed.",
        "resume": "Resumed. The next sync runs as usual.",
        "approve": f"Approved the shown change set ({e.get('count', '?')} of {e.get('total', '?')} files).",
        "cancel": "Cancelled the blocked sync. Nothing was written; the next sync checks again.",
        "restore": f"State restored from backup {e.get('generation', '?')} ({e.get('owned_files', '?')} owned files).",
        "move": f"Your output folder moved to GitHub {e.get('repo', '?')} ({e.get('branch', '?')}); the state of {e.get('owned_files', '?')} owned files was committed.",
        "change_target": f"Pipeline moved from {e.get('old', '?')} to {e.get('new', '?')}; the ownership of {e.get('owned_files', '?')} files was carried over. The old target was not touched.",
        "force": f"Forced {len(e.get('paths') or [])} file(s) over edits made in your output folder: " + ", ".join(e.get("paths") or []),
        "remove": f"Pipeline removed; {e.get('unowned', 0)} files kept in your output folder (this pipeline no longer updates them), {e.get('deleted', 0)} deleted.",
        "remove_source": f"Folder {e.get('source_label')} removed; its {e.get('unowned', 0)} files are kept in your output folder; this pipeline no longer updates them"
                         + (f"; {e.get('deleted', 0)} files deleted." if e.get("delete_files") else "."),
    }.get(action, str(action))


def _run_details(run: dict) -> list[dict]:
    op = {"keep": "skip"}
    return [{"op": op.get(f["action"], f["action"]), "target": f.get("target") or f.get("key", ""), "note": f.get("reason", "")}
            for f in run.get("files", [])]


def activity_view(brain: Brain, pipeline: str | None = None) -> dict:
    """Needs you + history. ``pipeline``: only that pipeline's cards and rows (view mode, and Activity filtered to a pipeline)."""
    config = load_config(brain)
    try:
        full_status = brain_status(brain, pipeline=pipeline)
        statuses = full_status["pipelines"]
    except StateError as exc:
        events = ActivityLog(brain).read()
        info = state_error_view(brain, exc)
        card = {"kind": "state", "message": info["message"], "backups": info["backups"],
                "title": "The ownership state (state.json) cannot be read", "cli_hint": "mdpipeline state restore"}
        return {"needs_you": [card], "history": _history(events, pipeline), "badge": 1}
    events = ActivityLog(brain).read()
    runs = [e for e in events if e["type"] == "run"]
    needs = [_conflict_target_card(s) for s in statuses if s.get("credential_conflict")]
    for s in statuses:
        if s["status"] == "error" and s["last_run"] and not s.get("credential_conflict"):
            needs += [_target_card(s)] if s["last_run"].get("category") else _error_cards(brain, s, config)
    for s in statuses:
        pending = s.get("pending_approval")
        if s["status"] == "blocked" and pending and pending.get("kind") != "remove_source":
            needs.append(_guard_card(brain, s, runs))
    needs += [_conflict_card(s, brain, config.get(s["name"])) for s in statuses if s["status"] == "conflicts" and s["conflicts"]]
    clones = [_clone_card(s["target"], s["name"]) for s in statuses if clone_needs_you(s["target"])]
    needs += clones
    imports = _import_cards(brain, pipeline)
    needs += imports
    return {"needs_you": needs, "history": _history(events, pipeline), "badge": count_open_items(statuses) + len(clones) + len(imports)}


def _import_cards(brain: Brain, pipeline: str | None = None) -> list[dict]:
    """One card when a GitHub target still reads its token through an old env:/keychain: reference (MD-87)."""
    from .secretimport import pending_imports

    items = [i for i in pending_imports(brain) if pipeline is None or pipeline in i.pipelines]
    if not items:
        return []
    return [{"kind": "import", "title": "Move your tokens into the app vault",
             "items": [{"ref": i.ref, "pipelines": i.pipelines, "secret_kind": i.secret_kind} for i in items],
             "message": "These pipelines still read a token (or a Jira email) from an environment variable or a keychain entry you created by hand. The app can keep it "
                        "in its vault instead; after that it never reads the old place again and you may delete it. Until you import, nothing breaks."}]


def _history(events: list[dict], pipeline: str | None = None) -> list[dict]:
    history = []
    for index, e in reversed(list(enumerate(events))):
        if pipeline is not None and e.get("pipeline") != pipeline:
            continue
        if e["type"] == "run":
            history.append({"id": index, "kind": "run", "ts": e["ts"], "pipeline": e["pipeline"], "trigger": e["trigger"],
                            "result": e["result"], "needs_you": only_missing_sources(e), "counts": e["counts"], "duration_ms": e["duration_ms"],
                            "details": _run_details(e), "errors": e.get("errors", []), "message": e.get("message", ""),
                            "sources": e.get("sources", {}), **({"commit": e["commit"]} if e.get("commit") else {}),
                            **({"clone": e["clone"]} if e.get("clone") else {})})
        elif e["type"] == "event":
            history.append({"id": index, "kind": "event", "ts": e["ts"], "pipeline": e.get("pipeline"), "trigger": e.get("trigger", ""),
                            "action": e.get("action"), "text": _event_text(e)})
        if len(history) >= HISTORY_LIMIT:
            break
    return history
