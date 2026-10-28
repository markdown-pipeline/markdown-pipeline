"""State recovery (MD-39): restore the last good state backup, after showing what the next sync would do.

The sync itself still refuses to run on an unreadable state (an empty state would orphan every file). Restoring is
the owner's explicit step: ``preview_restore`` plans every pipeline against the backup and writes nothing;
``apply_restore`` then swaps the backup in under the brain lock and keeps the replaced file aside.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .brain import Brain
from .config import load_config
from .destinations import state_home, target_group
from .locking import brain_lock
from .log import ActivityLog
from .planner import Plan, plan_pipeline
from .naming import collision_key
from .state import BACKUP_GENERATIONS, State, StateError, backup_path, install_state, parse_state, set_aside_state


class RestoreError(Exception):
    """There is nothing usable to restore (message is user-facing)."""


@dataclass
class RestorePreview:
    generation: int
    backup: Path | str                     # a backup file (local brain) or "commit abc1234" (repository brain)
    owned_files: int
    plans: dict[str, Plan] = field(default_factory=dict)
    applied: bool = False
    data: bytes | None = None              # repository brain: the state bytes to commit

    def to_dict(self) -> dict:
        return {"generation": self.generation, "backup": str(self.backup), "owned_files": self.owned_files,
                "applied": self.applied,
                "pipelines": {name: {"counts": plan.counts(), "guard": plan.guard.to_dict()} for name, plan in self.plans.items()}}


def repo_candidates(brain: Brain) -> list[tuple[str, bytes]]:
    """Repository brain: earlier valid states from the history of the state file, newest first (generation 1 = first entry).

    States equal to the current one are skipped, so restoring always goes back to something different."""
    target = brain.target
    target.begin()
    current = target.read_state()
    out: list[tuple[str, bytes]] = []
    for commit in target.history_of_state(20):
        data = target.state_at(commit["sha"])
        if data is None or data == current or (out and out[-1][1] == data):
            continue
        try:
            parse_state(data.decode("utf-8"))
        except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
            continue
        out.append((commit["sha"], data))
    return out


def usable_generations(brain: Brain) -> list[int]:
    """Backup generations (1 = newest) that parse as a valid state."""
    if brain.target.state_in_repo:
        return list(range(1, len(repo_candidates(brain)) + 1))
    home = state_home(brain)
    out = []
    for generation in range(1, BACKUP_GENERATIONS + 1):
        if not backup_path(home, generation).exists():
            continue
        try:
            State.load_backup(home, generation)
        except StateError:
            continue
        out.append(generation)
    return out


def _choose(brain: Brain, generation: int | None) -> tuple[int, State]:
    usable = usable_generations(brain)
    if generation is not None:
        if generation not in usable:
            raise RestoreError(f"Backup generation {generation} is not usable. Usable generations: {usable or 'none'}.")
        return generation, State.load_backup(state_home(brain), generation)
    if not usable:
        raise RestoreError("No usable backup of the state was found in .mdpipeline/backups/. "
                           "A backup is made on each save once there is a previous valid state.")
    return usable[0], State.load_backup(state_home(brain), usable[0])


def _choose_repo(brain: Brain, generation: int | None) -> tuple[int, State, str, bytes]:
    candidates = repo_candidates(brain)
    if not candidates:
        raise RestoreError("No usable earlier state was found in the repository's history of .mdpipeline/state.json.")
    chosen = generation or 1
    if not 1 <= chosen <= len(candidates):
        raise RestoreError(f"Generation {chosen} is not usable. Usable generations: {list(range(1, len(candidates) + 1))}.")
    sha, data = candidates[chosen - 1]
    return chosen, State(brain, parse_state(data.decode("utf-8"))), f"commit {sha[:7]}", data


def preview_restore(brain: Brain, generation: int | None = None, pipeline: str | None = None) -> RestorePreview:
    """What the next sync of every pipeline of the target would do with the backup state. Writes nothing.

    The target is the one of ``pipeline``, or the only target in use (MD-78)."""
    config = load_config(brain)
    bound, group = target_group(brain, config, pipeline)
    return _preview_restore(bound, group, generation)


def _preview_restore(brain: Brain, group: list, generation: int | None) -> RestorePreview:
    data = None
    if brain.target.state_in_repo:
        chosen, restored, source, data = _choose_repo(brain, generation)
    else:
        chosen, restored = _choose(brain, generation)
        source = backup_path(state_home(brain), chosen)
    preview = RestorePreview(chosen, source, sum(len(p.files) for p in restored.pipelines.values()), data=data)
    for pipeline in group:
        others = {collision_key(t): name for name, ps in restored.pipelines.items() if name != pipeline.name for t in ps.files}
        preview.plans[pipeline.name] = plan_pipeline(brain, pipeline, restored.pipelines.get(pipeline.name) or State(brain).pipeline(pipeline.name),
                                                     now=time.time(), others=others)
    return preview


def apply_restore(brain: Brain, generation: int | None = None, trigger: str = "cli", pipeline: str | None = None) -> RestorePreview:
    """Replace state.json by the backup. The file it replaces is kept as backups/state-before-restore.json."""
    config = load_config(brain)
    bound, group = target_group(brain, config, pipeline)
    preview = _preview_restore(bound, group, generation)
    with brain_lock(brain):
        if preview.data is not None:                       # repository brain: one new commit; the old state stays in git history
            bound.target.begin()
            bound.target.write_state(preview.data)
            bound.target.commit(f"mdpipeline restore state from {preview.backup}")
        else:
            set_aside_state(state_home(bound), "state-before-restore.json")
            install_state(state_home(bound), preview.backup.read_bytes())
    preview.applied = True
    ActivityLog(brain).event("restore", None, trigger, generation=preview.generation, owned_files=preview.owned_files)
    return preview
