"""Per-pipeline state: which brain files a pipeline owns (``<brain>/.mdpipeline/state.json``).

Ownership = a record here (plus the file's provenance). Removing a pipeline drops its record, so
its files become unowned, and a new pipeline re-using the name does not re-own them.

``written_hash`` is the hash of the full bytes the pipeline last wrote to the brain file; it is how
edits made in the brain are detected (the provenance ``content_hash`` is of the SOURCE and cannot be).
``pending_hash`` is recorded before a write so an interrupted run converges on the next run.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .brain import Brain, ensure_config_dir

BACKUP_GENERATIONS = 5      # state-1.json (newest) .. state-5.json; the spec asks for at least 3
BACKUP_DIR = "backups"


class StateError(Exception):
    """The state file is unreadable; refusing to guess (an empty state would orphan every file)."""


def backups_dir(brain: Brain) -> Path:
    return brain.config_dir / BACKUP_DIR


def backup_path(brain: Brain, generation: int) -> Path:
    return backups_dir(brain) / f"state-{generation}.json"


def parse_state(text: str) -> dict[str, "PipelineState"]:
    """Parse the text of a state file. Raises ValueError/TypeError/AttributeError when it is not a valid state."""
    raw = json.loads(text)
    return {
        name: PipelineState(
            files={t: FileRecord(**rec) for t, rec in data.get("files", {}).items()},
            pending_approval=data.get("pending_approval"),
            blocked=bool(data.get("blocked", False)),
            review=dict(data.get("review") or {}),
        )
        for name, data in raw.get("pipelines", {}).items()
    }


@dataclass
class FileRecord:
    source_label: str
    source_path: str
    source_hash: str
    written_hash: str | None = None
    pending_hash: str | None = None
    # MD-35: the assigned name is decided once (at first copy) and kept; None for records of the "original" policy
    first_seen: str | None = None     # ISO date the pipeline first copied this source file
    name_policy: str | None = None    # the preset name or custom pattern the name was assigned under
    suffix: str | None = None         # the clash suffix (6 hex), when one was applied
    source_version: str | None = None # MD-45: the connector's change marker last written (None for folders)


@dataclass
class PipelineState:
    files: dict[str, FileRecord] = field(default_factory=dict)
    # set when a sync was blocked by the guard: {"change_set_id", "count", "total", "percent", "shown_at"}
    pending_approval: dict | None = None
    blocked: bool = False
    # MD-40: files a rebuild could not re-own: {brain path: {"source_label", "source_path"}}. The sync keeps their
    # name for that source and reports a conflict instead of writing a second copy.
    review: dict[str, dict] = field(default_factory=dict)


class State:
    def __init__(self, brain: Brain, pipelines: dict[str, PipelineState] | None = None) -> None:
        self.brain = brain
        self.pipelines = pipelines or {}

    @property
    def path(self):
        return self.brain.config_dir / "state.json"

    @classmethod
    def load(cls, brain: Brain) -> "State":
        """Read the state through the brain's target. A target that cannot be read raises ``TargetError`` (never an empty state)."""
        target = brain.target
        path = brain.config_dir / "state.json" if not target.state_in_repo else target.describe()
        try:
            data = target.read_state()
            if data is None:
                return cls(brain, _with_runtime(brain, {}))
            pipelines = parse_state(data.decode("utf-8"))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise StateError(
                f"Cannot read {path}: {exc}. Nothing was changed. Restore the last good copy with "
                "`mdpipeline state restore` (it first shows what the next sync would do; add --apply to restore), "
                "or in the UI under Needs you. Deleting the file instead would make every copied file unowned.") from exc
        return cls(brain, _with_runtime(brain, pipelines))

    @classmethod
    def load_backup(cls, brain: Brain, generation: int) -> "State":
        """The state stored in backup ``generation`` (1 = newest). Raises StateError when it is missing or invalid."""
        path = backup_path(brain, generation)
        try:
            return cls(brain, parse_state(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise StateError(f"Backup {path} cannot be used: {exc}") from exc

    def pipeline(self, name: str) -> PipelineState:
        return self.pipelines.setdefault(name, PipelineState())

    def drop_pipeline(self, name: str) -> None:
        self.pipelines.pop(name, None)

    def save(self) -> None:
        """Serialise and hand the bytes to the target (the one place that decides where the state lives)."""
        in_repo = self.brain.target.state_in_repo
        data = {"version": 1, "pipelines": {
            name: {"files": {t: _record_dict(r) for t, r in ps.files.items()},
                   **({} if in_repo else {"pending_approval": ps.pending_approval, "blocked": ps.blocked}),
                   **({"review": ps.review} if ps.review else {})}
            for name, ps in self.pipelines.items()}}
        if in_repo:
            _save_runtime(self.brain, self.pipelines)
            if not any(ps.files or ps.review for ps in self.pipelines.values()) and self.brain.target.read_state() is None:
                return          # a repository gets no state commit that records nothing (e.g. a first sync where every file failed)
        self.brain.target.write_state(json.dumps(data, indent=1, sort_keys=True).encode("utf-8"))


def _keep_previous_as_backup(brain: Brain) -> None:
    """Before overwriting, keep the current state as backup 1 (shifting older ones) if it is valid and new."""
    try:
        current = (brain.config_dir / "state.json").read_bytes()
        parse_state(current.decode("utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        return        # nothing to keep, or the current file is corrupt: never rotate a bad file over a good backup
    newest = backup_path(brain, 1)
    try:
        if newest.read_bytes() == current:
            return    # nothing changed since the last save: do not waste a generation
    except OSError:
        pass
    directory = backups_dir(brain)
    directory.mkdir(exist_ok=True)
    for generation in range(BACKUP_GENERATIONS - 1, 0, -1):
        older = backup_path(brain, generation)
        if older.exists():
            os.replace(older, backup_path(brain, generation + 1))
    tmp = directory / f"{uuid.uuid4().hex}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(current)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, newest)
    ignore = brain.config_dir / ".gitignore"
    try:
        lines = ignore.read_text(encoding="utf-8").split() if ignore.exists() else []
        if f"{BACKUP_DIR}/" not in lines:
            with open(ignore, "a", encoding="utf-8") as handle:
                handle.write(("" if not lines or ignore.read_text(encoding="utf-8").endswith("\n") else "\n") + f"{BACKUP_DIR}/\n")
    except OSError:
        pass


def _record_dict(record: FileRecord) -> dict:
    """The saved form of a record: ``source_version`` is omitted when empty, so folder state stays byte-identical to before."""
    data = asdict(record)
    if data.get("source_version") is None:
        del data["source_version"]
    return data


def write_local_state(brain: Brain, data: bytes) -> None:
    """Local target: keep the previous valid state as a backup generation, then atomically replace state.json."""
    ensure_config_dir(brain)
    _keep_previous_as_backup(brain)
    path = brain.config_dir / "state.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


RUNTIME_FILE = "runtime.json"     # local only: what a repository target must not commit (guard approvals in progress)


def _with_runtime(brain: Brain, pipelines: dict[str, "PipelineState"]) -> dict[str, "PipelineState"]:
    if not brain.target.state_in_repo:
        return pipelines
    try:
        raw = json.loads((brain.config_dir / RUNTIME_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return pipelines
    for name, extra in raw.items():
        ps = pipelines.setdefault(name, PipelineState())
        ps.pending_approval, ps.blocked = extra.get("pending_approval"), bool(extra.get("blocked", False))
    return pipelines


def _save_runtime(brain: Brain, pipelines: dict[str, "PipelineState"]) -> None:
    data = {name: {"pending_approval": ps.pending_approval, "blocked": ps.blocked}
            for name, ps in pipelines.items() if ps.pending_approval or ps.blocked}
    path = brain.config_dir / RUNTIME_FILE
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def set_aside_state(brain: Brain, name: str) -> None:
    """Move the current state.json (if any) to ``backups/<name>`` so a restore or rebuild never destroys it."""
    current = brain.config_dir / "state.json"
    if current.exists():
        backups_dir(brain).mkdir(exist_ok=True)
        os.replace(current, backups_dir(brain) / name)


def install_state(brain: Brain, data: bytes) -> None:
    """Atomically make ``data`` (the bytes of a valid state) the state.json."""
    tmp = brain.config_dir / "state.json.tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, brain.config_dir / "state.json")
