"""Removing a pipeline, or one folder of a pipeline, without losing brain files.

By default the brain files stay and become *unowned*: the pipeline's state records are dropped, so no
pipeline will ever modify or delete them again (a new pipeline with the same name does not re-own them).
Every delete goes through the brain's target (the local one uses ``SafeWriter``). Purging deletes only owned files that were not edited in the brain, after a confirmation that names the
count. Deleting a folder's files additionally goes through the big-change guard.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .boundary import check_write_target
from .brain import Brain
from .config import load_config, save_config
from .destinations import bind_target
from .engine import APPROVING_TRIGGERS
from .guard import GuardState, evaluate_guard
from .locking import brain_lock
from .logsafe import log_paths
from .log import ActivityLog, now_iso
from .planner import _brain_file_hash, _effective_written
from .service import NotFound, ValidationFailed
from .state import FileRecord, State
from .validation import Issue, ValidationResult
from .writer import WriteRefused


class ConfirmationRequired(Exception):
    """Purging needs an explicit confirmation naming the number of files."""

    def __init__(self, count: int, name: str) -> None:
        super().__init__(f"Purging pipeline '{name}' deletes {count} files. Confirm with the count ({count}).")
        self.count = count


@dataclass
class RemovalResult:
    result: str = "ok"                       # ok | blocked
    unowned: int = 0                         # files kept in the brain, now owned by nobody
    deleted: int = 0
    left_edited: list[str] = field(default_factory=list)   # owned files kept because they were edited in the brain
    guard: GuardState | None = None
    message: str = ""
    affected: list[str] = field(default_factory=list)


def _classify(brain: Brain, files: dict[str, FileRecord]) -> tuple[list[str], list[str], list[str]]:
    """Split records into (deletable clean files, edited files, already gone)."""
    clean, edited, gone = [], [], []
    for target, rec in sorted(files.items()):
        kind, current = _brain_file_hash(brain, target)
        if kind == "absent":
            gone.append(target)
        elif kind == "file" and _effective_written(rec, current) is not None:
            clean.append(target)
        else:
            edited.append(target)
    return clean, edited, gone


def remove_pipeline(brain: Brain, name: str, *, trigger: str, purge: bool = False,
                    confirm_count: int | None = None) -> RemovalResult:
    config = load_config(brain)
    pipeline = config.get(name)
    if pipeline is None:
        raise NotFound(name)
    bound = bind_target(brain, pipeline)
    with brain_lock(brain):
        bound.target.begin()
        state = State.load(bound)
        files = dict(state.pipeline(name).files)
        res = RemovalResult()
        if purge:
            if confirm_count != len(files):
                raise ConfirmationRequired(len(files), name)
            clean, edited, _ = _classify(bound, files)
            for target in clean:
                _delete(bound, target)
            res.deleted, res.left_edited = len(clean), edited
            res.unowned = len(edited)
        else:
            _, _, gone = _classify(bound, files)
            res.unowned = len(files) - len(gone)
        kept = sorted(set(files) - set(clean if purge else []))
        state.drop_pipeline(name)
        state.save()
        bound.target.commit(f"mdpipeline remove [{name}]: {res.deleted} deleted" + (" (purge)" if purge else ""))
        config.pipelines = [p for p in config.pipelines if p.name != name]
        save_config(brain, config)
    present = [p for p in kept if p not in _gone_set(bound, kept)]
    ActivityLog(brain).event("remove", name, trigger, unowned=res.unowned, deleted=res.deleted, purge=purge,
                             paths=log_paths(pipeline, {p: files[p] for p in present}))
    return res


@dataclass
class PipelinePreview:
    """What removing a whole pipeline would do (nothing is written): the exact numbers shown before anything is deleted."""
    owned: int = 0                # files the pipeline owns (still present or not)
    deletable: int = 0            # files a purge would delete: owned, present and not edited in the brain
    edited: list[str] = field(default_factory=list)       # owned files edited in the brain: always kept


def preview_remove_pipeline(brain: Brain, name: str) -> PipelinePreview:
    pipeline = load_config(brain).get(name)
    if pipeline is None:
        raise NotFound(name)
    bound = bind_target(brain, pipeline)
    bound.target.begin()
    files = dict(State.load(bound).pipeline(name).files)
    clean, edited, _ = _classify(bound, files)
    return PipelinePreview(owned=len(files), deletable=len(clean), edited=edited)


def _delete(brain: Brain, rel: str) -> None:
    reason = check_write_target(rel)
    if reason:
        raise WriteRefused(f"Refused {rel!r}: {reason}.")
    brain.target.delete(rel)


def _gone_set(brain: Brain, targets: list[str]) -> set[str]:
    return {t for t in targets if brain.target.read_file(t).kind == "absent"}


@dataclass
class RemovalPreview:
    """What removing one source would do (nothing is written): used by the CLI's --dry-run."""
    kept: int = 0                 # files that stay in the brain, owned by nobody
    deleted: int = 0              # files that would be deleted (only with delete_files)
    left_edited: int = 0          # owned files edited in the brain: always kept
    guard: GuardState | None = None


def preview_remove_source(brain: Brain, name: str, label: str, *, delete_files: bool = False) -> RemovalPreview:
    config = load_config(brain)
    pipeline = config.get(name)
    if pipeline is None or not any(s.label == label for s in pipeline.sources):
        raise NotFound(f"{name}/{label}")
    bound = bind_target(brain, pipeline)
    bound.target.begin()
    ps = State.load(bound).pipeline(name)
    mine = {t: r for t, r in ps.files.items() if r.source_label == label}
    clean, edited, gone = _classify(bound, mine)
    if not delete_files:
        return RemovalPreview(kept=len(mine) - len(gone))
    guard = evaluate_guard(len(clean), len(ps.files), pipeline.guard_threshold_percent, pipeline.guard_min_files)
    return RemovalPreview(kept=len(edited), deleted=len(clean), left_edited=len(edited), guard=guard)


def remove_source(brain: Brain, name: str, label: str, *, trigger: str, delete_files: bool = False,
                  approve: bool = False, allow_empty: bool = False) -> RemovalResult:
    """Remove one source (any type) by label. ``allow_empty``: the caller saves a replacement source right after (a switch of source
    type in one save); otherwise the last source cannot be removed."""
    config = load_config(brain)
    pipeline = config.get(name)
    if pipeline is None or not any(s.label == label for s in pipeline.sources):
        raise NotFound(f"{name}/{label}")
    if len(pipeline.sources) == 1 and not allow_empty:
        raise ValidationFailed(ValidationResult(errors=[Issue(
            "no-sources", "A pipeline needs at least one source: add another one first, or remove the pipeline instead.", "sources")]))
    log = ActivityLog(brain)
    bound = bind_target(brain, pipeline)
    with brain_lock(brain):
        bound.target.begin()
        state = State.load(bound)
        ps = state.pipeline(name)
        mine = {t: r for t, r in ps.files.items() if r.source_label == label}
        res = RemovalResult()
        if delete_files:
            clean, edited, gone = _classify(bound, mine)
            guard = evaluate_guard(len(clean), len(ps.files), pipeline.guard_threshold_percent, pipeline.guard_min_files)
            change_id = "rm-" + hashlib.sha256("\n".join(f"{label}|{t}" for t in clean).encode()).hexdigest()[:16]
            if guard.blocked:
                pending = ps.pending_approval
                can_approve = approve and trigger in APPROVING_TRIGGERS
                if not (can_approve and pending and pending.get("change_set_id") == change_id):
                    ps.pending_approval = {"change_set_id": change_id, "count": guard.count, "total": guard.total,
                                           "percent": round(guard.percent, 1), "shown_at": now_iso(),
                                           "kind": "remove_source", "source_label": label}
                    state.save()
                    return RemovalResult("blocked", guard=guard, affected=clean,
                                         message=guard.message.replace("This sync", "Removing this folder's files"))
                log.event("approve", name, trigger, change_set_id=change_id, count=guard.count, total=guard.total)
            for target in clean:
                _delete(bound, target)
            res.deleted, res.left_edited, res.unowned = len(clean), edited, len(edited)
            res.guard = guard
            ps.pending_approval = None
        else:
            _, _, gone = _classify(bound, mine)
            res.unowned = len(mine) - len(gone)
        for target in mine:
            ps.files.pop(target, None)
        state.save()
        bound.target.commit(f"mdpipeline remove-source [{name}/{label}]: {res.deleted} deleted")
        pipeline.sources = [s for s in pipeline.sources if s.label != label]
        save_config(brain, config)
    log.event("remove_source", name, trigger, source_label=label, unowned=res.unowned, deleted=res.deleted,
              delete_files=delete_files)
    return res
