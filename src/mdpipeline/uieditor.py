"""Backend of the pipeline editor: folder checks, the live dry-run preview and saving."""
from __future__ import annotations

import os
import time

from .brain import Brain
from .config import Pipeline, Source, load_config, normalise_path
from .destinations import bind_target, clean_destination, destination_key, destination_type, ephemeral_github_view
from .guard import evaluate_guard
from .naming import DEFAULT_NAME_POLICY
from .planner import LINKS_NOTE, Plan, PlanItem, plan_pipeline
from .removal import remove_source
from .service import NotFound, ValidationFailed, update_pipeline
from .targetsetup import create_pipeline_with_token, drop_token, held_token
from .state import PipelineState, State
from .target import TargetError
from .uiapi import count_markdown, run_sync
from .validation import Issue, ValidationResult, validate_pipeline

TEMPLATE_ERROR_CODES = ("bad-template", "target-not-allowed")


def _lines(value) -> list[str]:
    if isinstance(value, str):
        value = value.splitlines()
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def draft_to_pipeline(d: dict, keep: Pipeline | None = None) -> Pipeline:
    """A draft as a Pipeline. A draft that names no file-name policy gets the default for NEW pipelines (MD-148); when it edits
    an existing pipeline (``keep``) it keeps that pipeline's policy, so an upgrade never renames anything."""
    from .config import DEFAULT_EXCLUDE, DEFAULT_GUARD_MIN_FILES, DEFAULT_GUARD_PERCENT, DEFAULT_INCLUDE, NEW_PIPELINE_TARGET

    sources = []
    for s in d.get("sources") or []:
        if s.get("type") == "jira":
            from .uijira import typed_values

            site = str(s.get("site", "")).strip().rstrip("/")
            options = {"site": site, "jql": str(s.get("jql", "")).strip(), "email_ref": str(s.get("email_ref", "")).strip(),
                       "token_ref": str(s.get("token_ref", "")).strip()}
            email, token = typed_values(s)
            if email:
                options["_email"] = email                      # typed values stay in memory (the "_" options are never saved)
            if token:
                options["_token"] = token
            sources.append(Source(site, str(s.get("label", "")).strip() or "jira", type="jira", options=options, enabled=s.get("enabled", True) is not False))
            continue
        src = Source(str(s.get("path", "")).strip() or ".", str(s.get("label", "")).strip())
        if not str(s.get("path", "")).strip():
            src.path = ""
        if not str(s.get("label", "")).strip():
            src.label = "" if not src.path else os.path.basename(src.path)
        src.enabled = s.get("enabled", True) is not False
        sources.append(src)
    return Pipeline(
        name=str(d.get("name", "")).strip(),
        sources=sources,
        target=NEW_PIPELINE_TARGET if d.get("target") is None else str(d["target"]).strip(),   # MD-140: missing = raw/{relpath}; "" = the root (Pipeline turns it into ROOT_TARGET)
        include=_lines(d.get("include")) or list(DEFAULT_INCLUDE),
        exclude=_lines(d["exclude"]) if "exclude" in d else list(DEFAULT_EXCLUDE),
        on_source_delete=d.get("on_source_delete", "delete"),
        guard_threshold_percent=float(d.get("guard_threshold_percent", DEFAULT_GUARD_PERCENT)),
        guard_min_files=int(d.get("guard_min_files", DEFAULT_GUARD_MIN_FILES)),
        name_policy=str(d.get("name_policy") or (keep.name_policy if keep else DEFAULT_NAME_POLICY)),
        name_pattern=str(d.get("name_pattern") or (keep.name_pattern if keep and not d.get("name_policy") else "")).strip(),
        destination=clean_destination(d.get("destination")),
    )


def _source_draft(s: Source) -> dict:
    """A saved source as the editor edits it. Jira: the credential *references* only (never a token)."""
    if s.type == "jira":
        o = s.options
        return {"type": "jira", "label": s.label, "path": s.path, "site": o.get("site", ""), "jql": o.get("jql", ""),
                "email_ref": o.get("email_ref", ""), "token_ref": o.get("token_ref", ""), "enabled": s.enabled}
    return {"path": s.path, "label": s.label, "enabled": s.enabled}


def pipeline_to_draft(p: Pipeline) -> dict:
    return {"name": p.name, "sources": [_source_draft(s) for s in p.sources], "target": p.target,
            "include": list(p.include), "exclude": list(p.exclude), "on_source_delete": p.on_source_delete,
            "guard_threshold_percent": p.guard_threshold_percent, "guard_min_files": p.guard_min_files,
            "name_policy": p.name_policy, "name_pattern": p.name_pattern, "destination": dict(p.destination) if p.destination else None}


def folder_check(path: str) -> dict:
    full = normalise_path(path) if path.strip() else ""
    if not full or not os.path.exists(full):
        return {"exists": False, "is_dir": False, "md_files": 0, "path": full}
    if not os.path.isdir(full):
        return {"exists": True, "is_dir": False, "md_files": 0, "path": full}
    return {"exists": True, "is_dir": True, "md_files": count_markdown(full), "path": full}


def validation_view(v: ValidationResult) -> dict:
    return {"errors": [vars(i) for i in v.errors], "warnings": [vars(i) for i in v.warnings],
            "can_save": v.ok, "can_save_and_sync": v.can_save_and_sync, "file_count": v.file_count}


def _op(item: PlanItem) -> tuple[str, str]:
    if item.action == "unchanged":
        return "skip", "unchanged"
    if item.action == "keep":
        return "skip", item.reason
    if item.action == "skip":
        if item.reason.startswith("target collision"):
            return "collision", item.reason
        return "skip", item.reason
    return item.action, item.reason


def _item_view(item: PlanItem, removed_note: str | None = None) -> dict:
    op, note = _op(item)
    gone = item.action in ("delete", "keep") or item.source_path == ""
    return {"op": op, "source": "" if gone else (item.display or item.source_path), "target": item.target, "note": note, "error": item.error}


def _blank_counts() -> dict:
    return dict.fromkeys(("create", "update", "rename", "delete", "skip", "conflict", "collision"), 0)


def preview(brain: Brain, body: dict) -> dict:
    """Validate a draft and compute its dry-run, grouped by source folder. Writes nothing."""
    editing = body.get("editing")
    pipeline = draft_to_pipeline(body.get("draft") or {}, keep=load_config(brain).get(editing) if editing else None)
    delete_labels = set(body.get("delete_labels") or [])
    config = load_config(brain)
    from .jirasetup import has_typed, with_placeholders

    validation = validate_pipeline(brain, config, with_placeholders(pipeline) if has_typed(pipeline) else pipeline, editing=editing)
    out = {"validation": validation_view(validation), "preview": {"groups": [], "counts": {"all": 0, **_blank_counts()},
                                                                    "guard": None, "example": None, "rename_note": ""}}
    if any(i.code in TEMPLATE_ERROR_CODES or i.code in ("bad-label", "duplicate-label") for i in validation.errors):
        return out
    saved_pipeline = config.get(editing) if editing else None
    dest = pipeline.destination or {}
    from .uitargets import unpack

    held, _ = unpack(dest.get("auth", "token"), held_token(dest.get("pending")))
    if destination_type(dest) == "github" and any(e["code"] == "no-credential" for e in out["validation"]["errors"]):
        # a typed token or a sign-in comes with the save: the live preview does not need it to be valid yet
        out["validation"]["errors"] = [e for e in out["validation"]["errors"] if e["code"] != "no-credential"]
        out["validation"]["can_save"] = not out["validation"]["errors"]
        out["validation"]["can_save_and_sync"] = out["validation"]["can_save"] and out["validation"]["file_count"] > 0
    if destination_type(dest) == "github" and not dest.get("credential") and not held:
        out["target_pending"] = True                 # the repository cannot be read until a token is tested (or a sign-in is done)
        return out
    try:
        # the files a saved pipeline owns are read from ITS saved target; the plan runs against the draft's target, so a
        # change of target shows what the new target needs (0 changes when the files are already there)
        bound = ephemeral_github_view(brain, dest, held) if held and not dest.get("credential") else bind_target(brain, pipeline)
        bound.target.begin()
        state = State.load(bound)
        if saved_pipeline is not None and bind_target(brain, saved_pipeline) is not bound:
            old = bind_target(brain, saved_pipeline)
            old.target.begin()
            state = State.load(old)
            bound.target.begin()
    except TargetError as exc:                 # the repository cannot be read right now: say so, never fail the editor
        out["target_error"] = {"message": str(exc), "category": exc.category}
        return out
    saved = state.pipelines.get(editing) if editing else None
    ps = PipelineState(files=dict(saved.files)) if saved else PipelineState()
    kept_labels = {s.label for s in pipeline.sources}
    removed_labels = sorted({r.source_label for r in ps.files.values()} - kept_labels)
    owned_removed = {lab: {t: r for t, r in ps.files.items() if r.source_label == lab} for lab in removed_labels}
    planning_ps = PipelineState(files={t: r for t, r in ps.files.items() if r.source_label in kept_labels})
    try:
        plan: Plan = plan_pipeline(bound, pipeline, planning_ps, now=time.time(), shallow=bool(body.get("shallow")))
    except TargetError as exc:
        out["target_error"] = {"message": str(exc), "category": exc.category}
        return out

    if saved_pipeline is not None and destination_key(brain, saved_pipeline) != destination_key(brain, pipeline):
        from .retarget import ChangeRefused, preview_change

        clean = {k: v for k, v in dest.items() if k != "pending"} if dest else None
        try:
            out["change"] = preview_change(brain, editing, clean, new_view=bound if held and not dest.get("credential") else None).to_dict()
        except ChangeRefused as exc:
            out["change_error"] = str(exc)
    groups = []
    by_key = {}
    for g in plan.groups:
        view = {"label": g.label, "path": g.path, "status": g.status, "error": g.error, "category": g.category, "removed": False,
                "md_files": sum(1 for i in g.items if i.target or i.action == "skip"), "items": [], "counts": _blank_counts()}
        for item in g.items:
            iv = _item_view(item)
            view["items"].append(iv)
            by_key[(g.label, item.source_path)] = iv
        groups.append(view)
    for g, view in zip(plan.groups, groups):  # flip the earlier side of a collision too
        for item in g.items:
            if item.reason.startswith("target collision with "):
                other = item.reason[len("target collision with "):]
                label, _, rel = other.partition(":")
                earlier = by_key.get((label, rel))
                if earlier and earlier["op"] != "collision":
                    earlier["op"], earlier["note"] = "collision", f"same target as {g.label}: {item.source_path}"
                    earlier["error"] = True
    removed_delete = 0
    for label in removed_labels:
        recs = owned_removed[label]
        deleting = label in delete_labels
        items = [{"op": "delete" if deleting else "skip", "source": "", "target": t,
                  "note": 'removed folder: "also delete" is checked' if deleting else "kept · becomes unowned (reported)", "error": False}
                 for t in sorted(recs)]
        removed_delete += len(items) if deleting else 0
        groups.append({"label": label, "path": "", "status": "ok", "error": "", "removed": True, "owned": len(recs),
                       "delete": deleting, "md_files": len(recs), "items": items, "counts": _blank_counts()})
    total = {"all": 0, **_blank_counts()}
    for view in groups:
        for iv in view["items"]:
            view["counts"][iv["op"]] += 1
            total[iv["op"]] += 1
            total["all"] += 1
    count = plan.guard.count + removed_delete
    guard = evaluate_guard(count, len(ps.files) if saved else plan.guard.total, pipeline.guard_threshold_percent, pipeline.guard_min_files)
    example = None
    for view in groups:
        first = next((i for i in view["items"] if i["source"] and i["target"] and i["op"] != "collision"), None)
        if first:
            example = {"source": f"{view['label']}: {first['source']}", "target": first["target"]}
            break
    out["preview"] = {"groups": groups, "counts": total, "guard": guard.to_dict(), "example": example,
                      "rename_note": LINKS_NOTE if total["rename"] else ""}
    return out


def pipeline_view(brain: Brain, name: str) -> dict:
    config = load_config(brain)
    p = config.get(name)
    if p is None:
        raise NotFound(name)
    state = State.load(bind_target(brain, p)).pipelines.get(name)
    owned: dict[str, int] = {s.label: 0 for s in p.sources}
    for rec in (state.files.values() if state else []):
        owned[rec.source_label] = owned.get(rec.source_label, 0) + 1
    from .uitargets import signin_login

    out = {"draft": pipeline_to_draft(p), "owned": owned, "paused": p.paused, "has_credential": bool((p.destination or {}).get("credential"))}
    login = signin_login(p)
    if login:
        out["signin"] = login
    return out


def create(brain: Brain, body: dict, sync_fn=None) -> dict:
    pipeline = draft_to_pipeline(body.get("draft") or {})
    pending = (pipeline.destination or {}).get("pending") or body.get("pending")
    token, tokens = None, None
    if destination_type(pipeline.destination) == "github":
        from .uitargets import unpack

        token, tokens = unpack((pipeline.destination or {}).get("auth", "token"), body.get("token") or held_token(pending))
    result = create_pipeline_with_token(brain, pipeline, token, tokens=tokens)
    drop_token(pending)
    for src in (body.get("draft") or {}).get("sources") or []:
        drop_token(src.get("pending"))
    sync, skipped = None, None
    if body.get("sync"):
        if result.file_count == 0:
            skipped = "0 files match the rules, so there is nothing to sync yet"
        else:
            sync = (sync_fn or (lambda n: run_sync(brain, n)))(pipeline.name)
    return {"saved": True, "name": pipeline.name, "warnings": [vars(i) for i in result.warnings], "sync": sync,
            "sync_skipped": skipped}


def _change_target(brain: Brain, name: str, pipeline: Pipeline, body: dict) -> None:
    """Move the pipeline to the draft's target (MD-85): checked, the ownership carried over, the old target untouched."""
    from . import vault as vault_mod
    from .retarget import ChangeRefused, apply_change
    from .targetconfig import GitHubSpec
    from .targetsetup import store_token
    from .uitargets import unpack

    dest = dict(pipeline.destination) if pipeline.destination else None
    created = None
    try:
        if destination_type(dest) == "github":
            pending = dest.pop("pending", None) or body.get("pending")
            token, tokens = unpack(dest.get("auth", "token"), body.get("token") or held_token(pending))
            if token:
                spec, created = store_token(GitHubSpec.from_dict(dest), token, f"GitHub {'sign-in' if tokens else 'token'} for {name}", tokens)
                clone = dest.get("clone")
                dest = spec.to_dict() | {"auth": spec.auth}
                if clone:
                    dest["clone"] = clone
            drop_token(pending)
        try:
            apply_change(brain, name, dest, trigger="ui")
        except ChangeRefused as exc:
            raise ValidationFailed(ValidationResult(errors=[Issue("change-target", str(exc), "destination")])) from None
    except Exception:
        if created:
            try:
                vault_mod.default_vault().remove(created)
            except vault_mod.VaultError:
                pass
        raise
    pipeline.destination = dest


IDENTITY = ("type", "repo", "branch", "path")


def _keep_target_identity(current: Pipeline, draft: Pipeline) -> None:
    """An edit never changes WHERE the pipeline writes (that is `change target`, with its own plan) and never loses the credential."""
    old, new = current.destination or {}, draft.destination or {}
    if destination_type(old) != destination_type(new) or any(old.get(k) != new.get(k) for k in IDENTITY if destination_type(old) == "github" or k == "path"):
        raise ValidationFailed(ValidationResult(errors=[Issue(
            "target-change", "Changing where a pipeline writes is done with Change target (it shows the plan first and leaves the old target untouched).", "destination")]))
    if old:
        merged = dict(old)
        merged.update({k: v for k, v in new.items() if k in ("auth", "clone")})
        if "clone" not in new:
            merged.pop("clone", None)
        draft.destination = merged


class _GuardBlocked(Exception):
    """A source removal that deletes files stopped at the big-change guard (nothing was changed)."""


def update(brain: Brain, name: str, body: dict, sync_fn=None) -> tuple[int, dict]:
    config = load_config(brain)
    current = config.get(name)
    if current is None:
        raise NotFound(name)
    pipeline = draft_to_pipeline(body.get("draft") or {}, keep=current)
    pipeline.name, pipeline.paused, pipeline.schedule = name, current.paused, current.schedule     # not form fields: an edit keeps them
    if body.get("change_target"):
        _change_target(brain, name, pipeline, body)
        current = load_config(brain).get(name)
        config = load_config(brain)
    _keep_target_identity(current, pipeline)
    from .jirasetup import has_typed, with_placeholders

    validation = validate_pipeline(brain, config, with_placeholders(pipeline) if has_typed(pipeline) else pipeline, editing=name)
    if not validation.ok:
        raise ValidationFailed(validation)
    blocked: list[dict] = []

    def remove_sources() -> None:
        """Runs after the new configuration passed every check, so a refused replacement source removes nothing."""
        for removal in body.get("remove_folders") or []:
            res = remove_source(brain, name, removal["label"], trigger="ui", delete_files=bool(removal.get("delete_files")),
                                approve=bool(body.get("approve")), allow_empty=bool(pipeline.sources))
            if res.result == "blocked":
                blocked.append({"result": "blocked", "message": res.message, "guard": res.guard.to_dict() if res.guard else None,
                                "affected": res.affected, "label": removal["label"]})
                raise _GuardBlocked

    try:
        result = update_pipeline(brain, name, pipeline, body.get("relabels") or None, after_validation=remove_sources)
    except _GuardBlocked:
        return 409, blocked[0]
    sync = None
    if body.get("sync") and result.file_count:
        sync = (sync_fn or (lambda n: run_sync(brain, n)))(name)
    return 200, {"saved": True, "name": name, "warnings": [vars(i) for i in result.warnings], "sync": sync}


class SourceChangeRefused(Exception):
    """A source change from the 'Needs you' notice that cannot be made (unknown label, not a folder, last source)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def set_source_path(brain: Brain, name: str, label: str, path: str) -> tuple[int, dict]:
    """MD-136 'Choose new location': give one folder source a new path. Same validation as the editor's Save (``update``); nothing is
    synced, so the next plan shows the result before any write."""
    pipeline = load_config(brain).get(name)
    if pipeline is None:
        raise NotFound(name)
    if not any(s.label == label and s.type == "folder" for s in pipeline.sources):
        raise SourceChangeRefused(f"Pipeline '{name}' has no folder source labelled '{label}'.", 404)
    check = folder_check(path)
    if not check["exists"]:
        raise SourceChangeRefused(f"Folder not found: {check['path'] or '(no path given)'}. Pick a folder that exists on this computer.")
    if not check["is_dir"]:
        raise SourceChangeRefused(f"{check['path']} is a file, not a folder.")
    draft = pipeline_to_draft(pipeline)
    for s in draft["sources"]:
        if s.get("label") == label:
            s["path"] = check["path"]
    return update(brain, name, {"draft": draft})


def remove_source_keep_files(brain: Brain, name: str, label: str) -> dict:
    """MD-136 'Remove this source': the folder leaves the pipeline, its files stay in the brain (unowned). Never the last source."""
    pipeline = load_config(brain).get(name)
    if pipeline is None:
        raise NotFound(name)
    if not any(s.label == label for s in pipeline.sources):
        raise SourceChangeRefused(f"Pipeline '{name}' has no source labelled '{label}'.", 404)
    if len(pipeline.sources) == 1:
        raise SourceChangeRefused("This is the only source of the pipeline. Remove the whole pipeline instead.", 409)
    res = remove_source(brain, name, label, trigger="ui", delete_files=False)
    return {"removed": True, "label": label, "kept": res.unowned}
