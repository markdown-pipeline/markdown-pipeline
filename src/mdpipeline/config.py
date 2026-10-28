"""Pipeline definitions and their YAML persistence (``<brain>/.mdpipeline/config.yaml``)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from .brain import Brain, ConfigReadError

DEFAULT_INCLUDE = ["**/*.md"]
DEFAULT_EXCLUDE = [".git/**", "node_modules/**", ".obsidian/**", "**/.*/**", "**/node_modules/**"]
DEFAULT_TARGET = "raw/{pipeline}/{source}/{relpath}"        # the template of pipelines saved before MD-133 (a saved config without a target keeps it)
NEW_PIPELINE_TARGET = "raw/{relpath}"                         # MD-140: what new pipelines get when the user sets nothing (saved explicitly in the config)
ROOT_TARGET = "{relpath}"                                     # MD-133: no template = the root of the chosen output folder; what new pipelines get
DEFAULT_GUARD_PERCENT = 40
DEFAULT_GUARD_MIN_FILES = 5
ON_DELETE_VALUES = ("delete", "keep")
SCHEDULE_VALUES = ("off", "15m", "1h", "6h", "daily")     # MD-121: how often a pipeline syncs by itself
DEFAULT_SCHEDULE = "1h"


def normalise_path(path: str) -> str:
    """Absolute, ``~`` expanded, no trailing slash. Symlinks are NOT resolved here."""
    return os.path.abspath(os.path.expanduser(path))


@dataclass
class Source:
    """One source of a pipeline: a folder (the default) or a connector (``type``, with its ``options``; MD-45)."""

    path: str
    label: str = ""
    type: str = "folder"
    options: dict = field(default_factory=dict)
    enabled: bool = True          # MD-103: an Off source keeps its setup but is not read; its files are kept and stay owned

    def __post_init__(self) -> None:
        if self.type == "folder":
            self.path = normalise_path(self.path)
        if not self.label:
            self.label = os.path.basename(self.path.rstrip("/"))

    def to_dict(self) -> dict:
        off = {} if self.enabled else {"enabled": False}          # absent means On, so existing configs are unchanged
        if self.type == "folder":
            return {"path": self.path, "label": self.label, **off}
        persisted = {k: v for k, v in self.options.items() if not k.startswith("_")}      # "_..." options live in memory only (typed, unsaved values)
        return {"type": self.type, "label": self.label, "path": self.path, **({"options": persisted} if persisted else {}), **off}

    def public_dict(self) -> dict:
        """How the source is shown to the user (CLI, UI, MCP): what it is, never a credential (only that references exist)."""
        if self.type == "jira":
            return {"type": "jira", "label": self.label, "path": self.path, "site": self.options.get("site", ""), "jql": self.options.get("jql", ""),
                    "enabled": self.enabled}
        return {"path": self.path, "label": self.label, **({"type": self.type} if self.type != "folder" else {}), "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict) -> "Source":
        return cls(data.get("path", ""), data.get("label", ""), data.get("type", "folder"), dict(data.get("options") or {}),
                   enabled=data.get("enabled", True) is not False)


@dataclass
class Pipeline:
    """One pipeline: source folders -> target template in the output folder (MD-133: the root unless a template says otherwise), with rules."""

    name: str
    sources: list[Source]
    target: str = DEFAULT_TARGET
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    on_source_delete: str = "delete"
    guard_threshold_percent: float = DEFAULT_GUARD_PERCENT
    guard_min_files: int = DEFAULT_GUARD_MIN_FILES
    paused: bool = False
    name_policy: str = "original"   # MD-33: pipelines saved before the feature behave as "original"
    name_pattern: str = ""          # only used by the "custom" policy
    # MD-78: where this pipeline's files land. None = a directory: the brain's own folder (``raw/`` there).
    # {"type": "directory", "path": ...} or {"type": "github", "repo", "branch", "credential", "clone"?}.
    # (The key is ``destination`` because ``target`` is the path template inside the output folder.)
    destination: dict | None = None
    schedule: str = DEFAULT_SCHEDULE      # MD-121: scheduled-sync interval; the default is not written to the file

    def __post_init__(self) -> None:
        if not (self.target or "").strip():          # MD-133: an empty template means the root of the output folder
            self.target = ROOT_TARGET

    @classmethod
    def new(cls, name: str, sources: list[str | Source], **rules) -> "Pipeline":
        """Create a pipeline; plain strings become sources whose label is the folder name."""
        return cls(name=name, sources=[s if isinstance(s, Source) else Source(s) for s in sources], **rules)

    def to_dict(self) -> dict:
        data = self._to_dict()
        if self.name_policy != "original":
            data["name_policy"] = self.name_policy
            if self.name_pattern:
                data["name_pattern"] = self.name_pattern
        if self.destination:
            data["destination"] = dict(self.destination)
        if self.schedule != DEFAULT_SCHEDULE:
            data["schedule"] = self.schedule
        return data

    def _to_dict(self) -> dict:
        return {
            "name": self.name,
            "sources": [s.to_dict() for s in self.sources],
            "target": self.target,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "on_source_delete": self.on_source_delete,
            "guard": {"threshold_percent": self.guard_threshold_percent, "min_files": self.guard_min_files},
            "paused": self.paused,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pipeline":
        guard = data.get("guard") or {}
        return cls(
            name=data["name"],
            sources=[Source.from_dict(s) for s in data.get("sources") or []],
            target=data.get("target", DEFAULT_TARGET),
            include=list(data.get("include", DEFAULT_INCLUDE)),
            exclude=list(data.get("exclude", DEFAULT_EXCLUDE)),
            on_source_delete=data.get("on_source_delete", "delete"),
            guard_threshold_percent=guard.get("threshold_percent", DEFAULT_GUARD_PERCENT),
            guard_min_files=guard.get("min_files", DEFAULT_GUARD_MIN_FILES),
            paused=bool(data.get("paused", False)),
            name_policy=data.get("name_policy", "original"),
            name_pattern=data.get("name_pattern", ""),
            destination=dict(data["destination"]) if data.get("destination") else None,
            schedule=data.get("schedule") if data.get("schedule") in SCHEDULE_VALUES else DEFAULT_SCHEDULE,
        )


@dataclass
class Config:
    """All pipelines of one brain."""

    pipelines: list[Pipeline] = field(default_factory=list)

    def get(self, name: str) -> Pipeline | None:
        return next((p for p in self.pipelines if p.name == name), None)


MIGRATION_BACKUP = "config-before-per-pipeline-target.yaml"


def _migrate_brain_level_target(brain: Brain, data: dict) -> dict:
    """MD-78: a configuration saved with a brain-level ``target`` gets that target as the ``destination`` of every pipeline.

    The original file is kept first (once, never overwritten); nothing else changes, so the first plan afterwards shows
    0 changes: ownership, first-seen dates and suffixes live in the state, which is untouched."""
    legacy = data["target"]
    backup = brain.config_dir / "backups" / MIGRATION_BACKUP
    try:
        backup.parent.mkdir(exist_ok=True)
        if not backup.exists():
            backup.write_bytes(brain.config_path.read_bytes())
    except OSError as exc:
        raise ConfigReadError(f"Cannot back up the configuration before migrating it ({exc}). Nothing was changed.") from exc
    migrated = {k: v for k, v in data.items() if k != "target"}
    migrated["pipelines"] = [{**p, "destination": dict(legacy)} if legacy else dict(p) for p in data.get("pipelines") or []]
    text = yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False, allow_unicode=True)
    tmp = brain.config_path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, brain.config_path)
    except OSError as exc:
        raise ConfigReadError(f"Cannot migrate the configuration {brain.config_path}: {exc}") from exc
    return migrated


def load_config(brain: Brain) -> Config:
    try:
        data = yaml.safe_load(brain.config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigReadError(f"Cannot read the configuration {brain.config_path}: {exc}") from exc
    if "target" in data:
        data = _migrate_brain_level_target(brain, data)
    return Config([Pipeline.from_dict(p) for p in data.get("pipelines") or []])


def save_config(brain: Brain, config: Config) -> None:
    """Atomically write the configuration (temp file + rename)."""
    document = {"version": 1, "pipelines": [p.to_dict() for p in config.pipelines]}
    text = yaml.safe_dump(document, sort_keys=False, default_flow_style=False, allow_unicode=True)
    tmp = brain.config_path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, brain.config_path)
