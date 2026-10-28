"""Change where a pipeline writes (MD-85): a folder to a GitHub repository, back, or to another folder or repository.

The plan for the new target is shown first (it is empty of changes when the files are already there, for example pushed by the
owner). Applying carries the pipeline's ownership records (names, first-seen dates, suffixes) to the new target as a state-only
change and switches the pipeline's configuration. **The old target is never changed or deleted**: not a file, and not its state file;
the result says what stays behind. Nothing here ever uploads ``raw/`` content: the next sync does that, through the plan.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .brain import Brain
from .config import Pipeline, load_config, save_config
from .destinations import bind_target, destination_key, destination_type, describe_destination
from .frontmatter import content_hash
from .locking import brain_lock
from .log import ActivityLog
from .naming import collision_key
from .planner import Plan, plan_pipeline
from .state import PipelineState, State
from .validation import validate_pipeline


class ChangeRefused(Exception):
    """The target cannot be changed (message is user-facing); nothing was changed."""


@dataclass
class ChangePreview:
    pipeline: str
    old_label: str
    new_label: str
    owned_files: int
    plan: Plan
    missing: list[str] = field(default_factory=list)       # owned, absent in the new target (the next sync would create them)
    differing: list[str] = field(default_factory=list)     # present in the new target with other bytes (a conflict; never overwritten)
    stays_behind: int = 0                                  # owned files that stay in the old target
    applied: bool = False

    def to_dict(self) -> dict:
        return {"pipeline": self.pipeline, "from": self.old_label, "to": self.new_label, "owned_files": self.owned_files,
                "missing": self.missing, "differing": self.differing, "stays_behind": self.stays_behind, "applied": self.applied,
                "counts": self.plan.counts(), "guard": self.plan.guard.to_dict(),
                "stays_behind_text": (f"{self.stays_behind} files and the state file stay untouched in {self.old_label}; "
                                      "the pipeline never changes or deletes them.")}


def _copy(ps: PipelineState) -> PipelineState:
    return PipelineState(files=dict(ps.files), review=dict(ps.review))


def _examine(brain: Brain, name: str, destination: dict | None, new_view: Brain | None = None):
    config = load_config(brain)
    pipeline = config.get(name)
    if pipeline is None:
        raise ChangeRefused(f"No pipeline named '{name}'.")
    moved = Pipeline.from_dict(pipeline.to_dict())
    moved.destination = destination
    if destination_key(brain, moved) == destination_key(brain, pipeline):
        raise ChangeRefused(f"Pipeline '{name}' already writes to that target.")
    checked = validate_pipeline(brain, config, moved, editing=name)
    if not checked.ok:
        raise ChangeRefused(" ".join(i.message for i in checked.errors))
    old = bind_target(brain, pipeline)
    old.target.begin()
    ps = _copy(State.load(old).pipeline(name))
    new = new_view or bind_target(brain, moved)
    new.target.begin()
    new_state = State.load(new)
    there = new_state.pipelines.get(name)
    if there is not None and there.files and there.files != ps.files:
        raise ChangeRefused(f"The new target already holds different ownership records for a pipeline named '{name}' (from an earlier use). "
                            "Nothing was changed. Rebuild that target's state first (`mdpipeline state rebuild`) or choose another target.")
    missing, differing = [], []
    for rel, rec in sorted(ps.files.items()):
        read = new.target.read_file(rel)
        if read.kind == "absent":
            missing.append(rel)
        elif read.kind != "file" or content_hash(read.data or b"") not in (rec.written_hash, rec.pending_hash):
            differing.append(rel)
    others = {collision_key(t): n for n, p in new_state.pipelines.items() if n != name for t in p.files}
    plan = plan_pipeline(new, moved, _copy(ps), now=time.time(), others=others)
    preview = ChangePreview(name, describe_destination(brain, pipeline)["label"], describe_destination(brain, moved)["label"],
                            len(ps.files), plan, missing, differing, len(ps.files))
    return preview, pipeline, moved, ps, new, new_state


def preview_change(brain: Brain, name: str, destination: dict | None, new_view: Brain | None = None) -> ChangePreview:
    """What the new target would need (the plan of the next sync there) and what stays behind. Writes nothing.

    ``new_view``: a Brain view of the new target (a typed token that is not stored yet); otherwise it is built from ``destination``."""
    return _examine(brain, name, destination, new_view)[0]


def apply_change(brain: Brain, name: str, destination: dict | None, trigger: str = "cli") -> ChangePreview:
    """Carry the ownership to the new target (state only) and switch the pipeline. The old target is not touched."""
    if destination_type(destination) == "github" and not (destination or {}).get("credential"):
        raise ChangeRefused("A GitHub target needs a token or a sign-in. Nothing was changed.")
    with brain_lock(brain):
        preview, pipeline, moved, ps, new, new_state = _examine(brain, name, destination)
        new_state.pipelines[name] = ps
        new_state.save()
        new.target.commit(f"mdpipeline change target: state of {len(ps.files)} owned files of pipeline '{name}'")
        config = load_config(brain)
        config.get(name).destination = moved.destination
        save_config(brain, config)
        brain._cache.clear()
    preview.applied = True
    ActivityLog(brain).event("change_target", name, trigger, owned_files=len(ps.files), old=preview.old_label, new=preview.new_label)
    return preview
