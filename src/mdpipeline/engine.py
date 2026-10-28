"""The sync engine: plan, then apply, then log. Used by the CLI, the MCP server and the UI.

Source- and destination-agnostic: the planner consumes ``SourceScan`` objects and every write goes
through the brain's ``Target`` (the local directory target uses ``SafeWriter``; the engine itself enforces the write
boundary for every target). A sync runs under the brain-wide lock; a second overlapping sync returns
``busy`` and writes nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from . import progress
from .boundary import check_write_target
from .brain import Brain
from .clone import fast_forward_clone
from .config import Config, Pipeline, load_config
from .destinations import bind_target, credential_conflict_message, credential_conflict_of
from .locking import BusyError, brain_lock
from .logsafe import connector_labels, item_key, log_conflict, log_error, log_file
from .log import ActivityLog, now_iso
from .planner import COUNT_KEYS, NEXT_STEP, Plan, PlanItem, plan_pipeline
from .state import FileRecord, State
from .target import CommitInfo, ConcurrentChange, CredentialConflict, TargetBusy, TargetError
from .targetmessages import with_hint
from . import usage_hooks
from .schedulestore import register_brain
from .writer import WriteRefused

APPROVING_TRIGGERS = ("cli", "ui")
MAX_ATTEMPTS = 3          # a target that keeps changing under a sync (someone else committing) is retried this often
COMMIT_PREFIX = "mdpipeline sync"
RESULTS = ("ok", "conflicts", "blocked", "cancelled", "error", "skipped: paused", "busy")


@dataclass
class SyncResult:
    pipeline: str
    trigger: str
    result: str
    dry_run: bool = False
    plan: Plan | None = None
    message: str = ""
    duration_ms: int = 0
    clone: dict | None = None            # the local clone's fast-forward outcome (GitHub brain with a clone): status + reason
    commit: CommitInfo | None = None     # a repository target: the commit this sync created (None: nothing to commit)
    category: str = ""                   # a target failure: auth, network, rate-limited, protected-branch, head-moved...
    approved: bool = False               # a blocked change set was approved and applied by this run (usage data: guard_resolved)

    def counts(self) -> dict[str, int]:
        return self.plan.counts() if self.plan else dict.fromkeys(COUNT_KEYS, 0)

    @property
    def errors(self) -> list[dict]:
        return self.plan.errors if self.plan else []

    @property
    def conflicts(self) -> list[dict]:
        return self.plan.conflicts if self.plan else []

    def to_dict(self) -> dict:
        d = {"pipeline": self.pipeline, "trigger": self.trigger, "result": self.result, "dry_run": self.dry_run,
             "message": self.message, "counts": self.counts(), "errors": self.errors, "conflicts": self.conflicts,
             "duration_ms": self.duration_ms}
        if self.commit:
            d["commit"] = self.commit.to_dict()
        if self.clone:
            d["clone"] = self.clone
        if self.category:
            d["category"] = self.category
        if self.plan:
            d["plan"] = self.plan.to_dict()
            d["guard"] = self.plan.guard.to_dict()
            d["change_set_id"] = self.plan.change_set_id
            if self.result == "blocked" or self.plan.guard.blocked:
                d["affected_files"] = [{"action": i.action, "target": i.target, "source_label": i.source_label,
                                        "source_path": i.source_path, **({"from": i.from_target} if i.from_target else {})}
                                       for i in self.plan.items() if i.action in ("delete", "update", "rename")]
        return d


def _outcome(plan: Plan, applied: bool = False) -> str:
    """Result of a plan. ``applied``: the plan was executed (e.g. after approval), so the guard no longer blocks."""
    if plan.guard.blocked and not applied:
        return "blocked"
    if plan.errors:
        return "error"
    if plan.counts()["conflicts"]:
        return "conflicts"
    return "ok"


def _log_run(log: ActivityLog, res: SyncResult, connectors: frozenset[str] = frozenset()) -> None:
    """Append the run to the activity log. Files of connector sources are logged by issue key, never by title (MD-71)."""
    items = res.plan.items() if res.plan else []
    errors = ([{"source": g.label, "error": g.error, **({"category": g.category} if g.category else {})} for g in res.plan.groups if g.status not in ("ok", "off")]
              + [log_error(i, i.source_label in connectors) for i in items if i.error]) if res.plan else []
    entry: dict = {"type": "run", "pipeline": res.pipeline, "trigger": res.trigger, "result": res.result,
                   "duration_ms": res.duration_ms, "counts": res.counts(), "errors": errors,
                   "conflicts": [{**log_conflict(i, i.source_label in connectors),
                                  **({"next_step": NEXT_STEP} if i.reason.endswith(NEXT_STEP) else {})}
                                 for i in items if i.action == "conflict"]}
    if res.message:
        entry["message"] = res.message
    if res.commit:
        entry["commit"] = res.commit.to_dict()
    if res.clone:
        entry["clone"] = res.clone
    if res.category:
        entry["category"] = res.category
    if res.plan:
        if res.plan.warnings:
            entry["warnings"] = res.plan.warnings
        entry["sources"] = {g.label: g.counts() for g in res.plan.groups}
        entry["guard"] = res.plan.guard.to_dict()
        entry["files"] = [log_file(i, i.source_label in connectors) for i in items if i.action != "unchanged" and not (i.action == "skip" and not i.error)]
    log.append(entry)


def sync_pipeline(brain: Brain, name: str, *, trigger: str = "cli", dry_run: bool = False, approve: bool = False,
                  force: bool = False, now: float | None = None,
                  force_paths: frozenset[str] | None = None,
                  after_write: Callable[[PlanItem], None] | None = None,
                  config: Config | None = None, scrub_paths: bool = False) -> SyncResult:
    """Sync one pipeline. ``after_write`` is a test hook called after each file write."""
    started = time.monotonic()
    config = config or load_config(brain)
    pipeline = config.get(name)
    if pipeline is None:
        raise KeyError(name)
    log = ActivityLog(brain)
    connectors = frozenset(connector_labels(pipeline))

    def finish(res: SyncResult, record: bool = True) -> SyncResult:
        res.duration_ms = int((time.monotonic() - started) * 1000)
        if record and not res.dry_run:
            _log_run(log, res, connectors)
            usage_hooks.sync_finished(res, pipeline)      # MD-116: content-free usage events; never raises, never waits
            register_brain(brain)                         # MD-121: after the first sync the background process keeps this brain current
        return res

    if pipeline.paused:
        return finish(SyncResult(name, trigger, "skipped: paused", dry_run, message="pipeline is paused"))

    clash = credential_conflict_of(brain, config, pipeline)
    if clash:                                          # MD-97: never write with a credential another pipeline's config did not choose
        return finish(SyncResult(name, trigger, "error", dry_run, message=credential_conflict_message(pipeline, clash), category=CredentialConflict.category))

    try:
        bound = bind_target(brain, pipeline)          # this pipeline's own target (MD-78); everything below runs against it
    except TargetError as exc:
        return finish(SyncResult(name, trigger, "error", dry_run, message=str(exc), category=exc.category))

    if dry_run:
        try:
            bound.target.begin()
            plan = plan_pipeline(bound, pipeline, State.load(bound).pipeline(name), now=now or time.time(), force=force, force_paths=force_paths, scrub_paths=scrub_paths)
        except TargetError as exc:
            return finish(SyncResult(name, trigger, "error", True, message=str(exc), category=exc.category))
        return finish(SyncResult(name, trigger, _outcome(plan), True, plan))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with brain_lock(brain):
                res = _run_locked(brain, bound, pipeline, trigger, approve, force, now, after_write, force_paths, scrub_paths)
                if res.result in ("ok", "conflicts"):
                    res.clone = _update_clone(bound)
                return finish(res)
        except BusyError as exc:
            return finish(SyncResult(name, trigger, "busy", message=str(exc)))
        except ConcurrentChange:
            continue                  # someone else changed the target meanwhile: nothing was written, plan again on the new head
        except TargetError as exc:
            return finish(SyncResult(name, trigger, "error", message=with_hint(str(exc), exc.category, name), category=exc.category))
    exc = TargetBusy(f"The target changed {MAX_ATTEMPTS} times while syncing, so nothing was written. Try again in a moment.")
    return finish(SyncResult(name, trigger, "error", message=str(exc), category=exc.category))


def _update_clone(brain: Brain) -> dict | None:
    """After a successful sync of a GitHub brain with a local clone: fast-forward it if that is safe (never fails the sync)."""
    target = brain.target
    clone = getattr(target, "clone", "")
    if not clone:
        return None
    return fast_forward_clone(str(clone), target.repo, target.branch).to_dict()


def _check_boundary(rel: str) -> None:
    """The write boundary, enforced here for every target (a target never has to remember it)."""
    reason = check_write_target(rel)
    if reason:
        raise WriteRefused(f"Refused {rel!r}: {reason}.")


def commit_message(pipeline: str, plan: Plan, approved_change_set: str | None = None) -> str:
    """The standard, machine-filterable message of a sync commit: fixed prefix, pipeline, counts, change-set id if approved."""
    c = plan.counts()
    subject = (f"{COMMIT_PREFIX} [{pipeline}]: {c['created']} created, {c['updated']} updated, "
               f"{c['renamed']} renamed, {c['deleted']} deleted")
    return subject + (f"\n\nchange-set: {approved_change_set}" if approved_change_set else "")


def _run_locked(brain: Brain, bound: Brain, pipeline: Pipeline, trigger: str, approve: bool, force: bool, now: float | None,
                after_write: Callable[[PlanItem], None] | None, force_paths: frozenset[str] | None = None, scrub_paths: bool = False) -> SyncResult:
    target = bound.target
    target.begin()
    target.clean_temp()
    state = State.load(bound)
    ps = state.pipeline(pipeline.name)
    plan = plan_pipeline(bound, pipeline, ps, now=now or time.time(), force=force, force_paths=force_paths, scrub_paths=scrub_paths)
    approved_id: str | None = None
    if plan.guard.blocked:
        pending = ps.pending_approval
        can_approve = approve and trigger in APPROVING_TRIGGERS     # the MCP can never approve
        approved = bool(can_approve and pending and pending.get("change_set_id") == plan.change_set_id)
        if not approved:
            message = plan.guard.message
            if can_approve and pending:
                message = "The change set changed since it was shown; review it and approve again. " + message
            elif can_approve:
                message = "Nothing was approved because this change set had not been shown yet; review it and approve again. " + message
            ps.pending_approval = {"change_set_id": plan.change_set_id, "count": plan.guard.count, "total": plan.guard.total,
                                   "percent": round(plan.guard.percent, 1), "shown_at": now_iso()}
            ps.blocked = True
            state.save()
            return SyncResult(pipeline.name, trigger, "blocked", False, plan, message=message)
        ActivityLog(brain).event("approve", pipeline.name, trigger, change_set_id=plan.change_set_id,
                                 count=plan.guard.count, total=plan.guard.total)
        approved_id = plan.change_set_id
    ps.pending_approval, ps.blocked = None, False
    items = plan.items()
    writes = [i for i in items if i.action in ("create", "update", "rename")]

    # Record intent first, so a crash between "file written" and "state saved" converges next run.
    for item in writes:
        rec = ps.files.get(item.target)
        if rec is None:
            ps.files[item.target] = FileRecord(item.source_label, item.source_path, item.source_hash or "", None, item.new_hash,
                                               item.first_seen, item.name_policy, item.suffix, item.source_version)
        else:
            rec.pending_hash = item.new_hash
    if writes:
        state.save()

    written = 0
    progress.report("writing", 0, len(writes))
    for item in writes:
        try:
            _check_boundary(item.target)
            target.write(item.target, item.new_bytes or b"")
        except WriteRefused as exc:
            item.error, item.reason = True, str(exc)
            continue
        except OSError as exc:
            item.error, item.reason = True, f"write failed: {exc}"
            continue
        ps.files[item.target] = FileRecord(item.source_label, item.source_path, item.source_hash or "", item.new_hash, None,
                                           item.first_seen, item.name_policy, item.suffix, item.source_version)
        ps.review.pop(item.target, None)
        if after_write:
            after_write(item)
        written += 1
        progress.report("writing", written)

    for item in items:
        if item.action == "rename" and not item.error and item.target in ps.files and ps.files[item.target].written_hash == item.new_hash:
            try:                      # the new copy is safely on disk and recorded: only now remove the old name
                _check_boundary(item.from_target or "")
                target.delete(item.from_target or "")
            except (WriteRefused, OSError) as exc:
                item.error, item.reason = True, str(exc)
                continue
            ps.files.pop(item.from_target or "", None)
        elif item.action == "delete":
            try:
                _check_boundary(item.target)
                target.delete(item.target)
            except (WriteRefused, OSError) as exc:
                item.error, item.reason = True, str(exc)
                continue
            ps.files.pop(item.target, None)
        elif item.action == "unchanged" and item.finalize_pending:
            rec = ps.files[item.target]
            rec.written_hash, rec.pending_hash = rec.pending_hash, None
        elif item.action == "unchanged" and item.source_version and item.target in ps.files:
            ps.files[item.target].source_version = item.source_version      # a new version whose rendering did not change
    for target in plan.forgotten:
        ps.files.pop(target, None)
    state.save()
    commit = target.commit(commit_message(pipeline.name, plan, approved_id))
    forced = [item_key(i) if i.source_label in connector_labels(pipeline) else i.target for i in writes if i.reason.startswith("forced") and not i.error]
    if forced:
        ActivityLog(brain).event("force", pipeline.name, trigger, paths=forced)
    return SyncResult(pipeline.name, trigger, _outcome(plan, applied=True), False, plan, commit=commit, approved=approved_id is not None)


def cancel_pending(brain: Brain, name: str, *, trigger: str) -> SyncResult:
    """Cancel the change set a blocked sync showed. The pipeline stays ``blocked`` until the next sync re-checks."""
    started = time.monotonic()
    pipeline = load_config(brain).get(name)
    if pipeline is None:
        raise KeyError(name)
    bound = bind_target(brain, pipeline)
    with brain_lock(brain):
        state = State.load(bound)
        ps = state.pipeline(name)
        pending = ps.pending_approval
        if not pending:
            raise ValueError(f"nothing to cancel for '{name}': no change set is waiting for approval")
        ps.pending_approval, ps.blocked = None, True
        state.save()
    res = SyncResult(name, trigger, "cancelled",
                     message=f"Cancelled: {pending['count']} of {pending['total']} files ({pending['percent']:g}%) left unchanged")
    res.duration_ms = int((time.monotonic() - started) * 1000)
    log = ActivityLog(brain)
    log.event("cancel", name, trigger, change_set_id=pending["change_set_id"])
    _log_run(log, res)
    usage_hooks.guard_cancelled()
    return res


def sync_all(brain: Brain, *, trigger: str = "cli", dry_run: bool = False, approve: bool = False,
             force: bool = False) -> list[SyncResult]:
    """Sync every pipeline. A blocked, paused or failing pipeline never stops the others."""
    config = load_config(brain)
    return [sync_pipeline(brain, p.name, trigger=trigger, dry_run=dry_run, approve=approve, force=force, config=config)
            for p in config.pipelines]
