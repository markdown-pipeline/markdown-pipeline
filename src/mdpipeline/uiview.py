"""Pipeline view mode in the UI (MD-92..94): the page of one saved pipeline and its next-sync dry run, read-only.

Nothing here writes: no config, no state, no file in any target. The configuration and status come from ``pipelineview`` (shared with the
CLI's ``pipeline show``); the HTTP layer only exposes these functions through GET routes.
"""
from __future__ import annotations

import datetime
import time

from .brain import Brain
from .config import Source, load_config
from .pipelineview import config_view, status_view
from .service import NotFound

RUNS_SHOWN = 10


def pipeline_detail(brain: Brain, name: str) -> dict:
    """The view-mode page of one pipeline: configuration, status, its open Needs-you items and its last runs. Pure reads."""
    from .uiactivity import activity_view

    p = load_config(brain).get(name)
    if p is None:
        raise NotFound(name)
    act = activity_view(brain, pipeline=name)
    runs = [h for h in act["history"] if h["kind"] == "run"][:RUNS_SHOWN]
    from .pipelinesummary import summary_sentence

    return {"config": config_view(brain, p), "status": {**status_view(brain, p), "vault_locked": any(n.get("category") == "vault-locked" for n in act["needs_you"])}, "needs_you": act["needs_you"], "runs": runs,
            "summary": summary_sentence(brain, p)}                # MD-135: generated here, kept out of config so `pipeline show --json` is unchanged


# --- the next-sync dry run (MD-93) -----------------------------------------------------------------------------------
def _iso(now: float) -> str:
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _target_problem(err: dict) -> dict:
    from .targetmessages import NEXT_STEPS

    category = err.get("category") or "error"
    title, step = NEXT_STEPS.get(category, NEXT_STEPS["error"])
    return {"kind": "target", "category": category, "title": title, "message": err.get("message", ""), "next_step": step}


def _source_problem(source: Source | None, group: dict) -> dict:
    """The "Needs you" wording of an unreadable source, the same words Activity uses."""
    if source is not None and source.type == "jira":
        from .uiactivity import _connector_error

        c = _connector_error(source, {"error": group["error"], "category": group.get("category") or "error"})
        return {"kind": "source", "label": group["label"], "category": c["category"], "title": c["kind_title"], "message": c["error"], "next_step": c["next_step"]}
    if group.get("category") == "source-missing":        # MD-136: a folder that is gone is a "Needs you" notice, not an error
        return {"kind": "source_missing", "label": group["label"], "path": group.get("path", ""), "category": "source-missing",
                "title": "Source folder not found", "message": group["error"], "next_step": ""}
    return {"kind": "source", "label": group["label"], "category": "folder-missing", "title": "Source folder cannot be read", "message": group["error"],
            "next_step": "Fix the path in Edit or restore the folder, then Sync now. Nothing it owns will be deleted."}


def plan_view(brain: Brain, name: str, now: float | None = None) -> dict:
    """The dry run of the SAVED configuration: the editor's preview (one plan engine), plus when it was made and the problems in plain words.

    Writes nothing. A source or target that cannot be read is reported as a problem and is never planned as a delete."""
    from .uieditor import pipeline_to_draft, preview

    p = load_config(brain).get(name)
    if p is None:
        raise NotFound(name)
    now = time.time() if now is None else now
    out = preview(brain, {"draft": pipeline_to_draft(p), "editing": name})
    by_label = {s.label: s for s in p.sources}
    problems, sources = [], {}
    if out.get("target_error"):
        problems.append(_target_problem(out["target_error"]))
    for g in out["preview"]["groups"]:
        if g.get("removed"):
            continue
        if g["status"] == "off":              # switched off by the owner: not read, not a problem
            sources[g["label"]] = {"ok": True, "off": True, "message": "Off: not read, its files are kept"}
            continue
        ok = g["status"] == "ok"
        sources[g["label"]] = {"ok": ok, "message": "Jira: connected" if ok and by_label.get(g["label"]) and by_label[g["label"]].type == "jira" else (g["error"] if not ok else "readable")}
        if not ok:
            problems.append(_source_problem(by_label.get(g["label"]), g))
    out["planned_at"] = _iso(now)
    out["problems"] = problems
    out["sources"] = sources
    return out
