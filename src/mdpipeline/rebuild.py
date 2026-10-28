"""Rebuild the ownership state from the provenance in ``raw/`` (MD-40), for when no backup is usable.

For every Markdown file in ``raw/`` whose provenance names a pipeline, a source folder and a source path, the
expected output is re-rendered from the CURRENT source and the provenance values that cannot be derived from it
(``synced_at``, ``source_modified``, ``source_root`` and the assigned-name fields). Only a byte-exact match is
re-owned. Anything else (an edited brain copy, a changed source, a hand-written note with copied provenance, a
pipeline or folder that no longer exists, two files claiming one source) is left unowned and listed. Names are
never recomputed: they are taken from the provenance. This module never writes to ``raw/``.
"""
from __future__ import annotations

import posixpath
import time
from dataclasses import dataclass, field

from .brain import Brain
from .config import Config, Pipeline, load_config
from .destinations import state_home, target_group
from .frontmatter import NAME_KEYS, FrontMatterError, build_provenance, content_hash, merge_provenance, parse_document
from .locking import brain_lock
from .log import ActivityLog
from .naming import collision_key
from .planner import Plan, plan_pipeline
from .sources import scan_source
from .state import FileRecord, State, StateError, set_aside_state
from .template import TemplateError, render_target

MISMATCH = "cannot tell whether the source or the copy in your output folder changed"


class RebuildRefused(Exception):
    """The rebuild cannot run (message is user-facing)."""


@dataclass
class Unclaimed:
    path: str
    reason: str


@dataclass
class RebuildPreview:
    claims: dict[str, dict[str, FileRecord]] = field(default_factory=dict)
    unclaimed: list[Unclaimed] = field(default_factory=list)
    review: dict[str, dict[str, dict]] = field(default_factory=dict)   # pipeline -> {path: identity}
    plans: dict[str, Plan] = field(default_factory=dict)
    applied: bool = False

    @property
    def claimed_total(self) -> int:
        return sum(len(c) for c in self.claims.values())

    @property
    def needs_approval(self) -> bool:
        return any(plan.guard.blocked for plan in self.plans.values())

    def to_dict(self) -> dict:
        return {"claimed": self.claimed_total, "applied": self.applied, "needs_approval": self.needs_approval,
                "unclaimed": [{"path": u.path, "reason": u.reason} for u in self.unclaimed],
                "pipelines": {n: {"counts": p.counts(), "guard": p.guard.to_dict()} for n, p in self.plans.items()}}


def _expected_dir(pipeline: Pipeline, label: str, rel: str, mtime: float) -> str:
    return posixpath.dirname(render_target(pipeline.target, pipeline=pipeline.name, source=label, relpath=rel, mtime=mtime))


def _examine(brain: Brain, config, sources: dict, rel: str):
    """Return ((pipeline, identity, record), None) when the file is claimable, else (None, reason | None)."""
    read = brain.target.read_file(rel)           # a target that cannot be read raises TargetError: never "no files"
    if read.kind != "file":
        return None, None, None
    data = read.data or b""
    try:
        meta, _ = parse_document(data.decode("utf-8"))
    except UnicodeDecodeError:
        return None, None, None
    prov = meta.get("provenance")
    if not isinstance(prov, dict) or not all(isinstance(prov.get(k), str) for k in ("pipeline", "source_label", "source_path")):
        return None, None, None                      # not something a pipeline wrote: ignored silently
    name, label, srel = prov["pipeline"], prov["source_label"], prov["source_path"]
    pipeline = config.get(name)
    if pipeline is None:
        return None, f"its provenance names pipeline '{name}', which does not exist", None
    if label not in {s.label for s in pipeline.sources}:
        return None, f"its provenance names '{label}', which is not a source folder of pipeline '{name}'", None
    scan = sources[(name, label)]
    if scan.status != "ok" and scan.kind != "folder":
        return None, f"the source '{label}' cannot be read now ({scan.error}); nothing is claimed while the site cannot confirm the content", None
    entry = next((e for e in scan.entries if e.identity == srel and e.status == "candidate"), None)
    if scan.status != "ok" or entry is None:
        return None, "its source file cannot be found now, so the expected content cannot be re-rendered", None
    try:
        expected_dir = _expected_dir(pipeline, label, entry.relpath, entry.mtime)
    except TemplateError:
        return None, "the pipeline's target template is invalid", None
    named = bool(prov.get("name_policy"))
    if posixpath.dirname(rel) != expected_dir or (not named and rel != posixpath.join(expected_dir, posixpath.basename(entry.relpath)).lstrip("/")):
        return None, "it is not where this pipeline writes that source file", None
    try:
        source_bytes = entry.read_bytes()
        expected = build_provenance(pipeline=name, source_label=label, source_path=srel, source_root=scan.path,
                                    source_mtime=0.0, synced_at=0.0, source_bytes=source_bytes,
                                    names={k: prov.get(k) for k in NAME_KEYS},
                                    source_type="local" if scan.kind == "folder" else scan.kind, extra=entry.provenance or None)
        for key in ("source_root", "source_modified", "synced_at"):
            if key in prov:
                expected[key] = prov[key]
        rendered = merge_provenance(source_bytes, expected)
    except (OSError, FrontMatterError):
        return None, "its source file cannot be re-rendered", None
    if rendered != data:
        return None, MISMATCH, (name, label, srel)
    record = FileRecord(label, srel, content_hash(source_bytes), content_hash(data), None,
                        prov.get("first_seen") if named else None, prov.get("name_policy") if named else None,
                        prov.get("name_suffix") if named else None)
    return (pipeline, (name, label, srel), record), None, None


def rebuild_preview(brain: Brain, pipeline: str | None = None) -> RebuildPreview:
    """Preview the rebuild of the state of one target: the target of ``pipeline``, or the only target in use (MD-78)."""
    config = load_config(brain)
    bound, group = target_group(brain, config, pipeline)
    return _rebuild_preview(bound, config, group)


def _rebuild_preview(brain: Brain, config, group: list[Pipeline]) -> RebuildPreview:
    brain.target.begin()
    try:
        existing = State.load(brain)
    except StateError:
        existing = None
    if existing is not None and any(ps.files for ps in existing.pipelines.values()):
        raise RebuildRefused("The state is readable and already owns files. Rebuilding is only for a missing or unreadable state "
                             "with no usable backup; use `mdpipeline state restore` to go back to a backup instead.")
    sources = {(p.name, s.label): scan_source(s, p.include, p.exclude) for p in group for s in p.sources}
    found: dict[tuple, list[tuple[str, FileRecord]]] = {}
    preview = RebuildPreview()
    for rel in brain.target.list_markdown():
        claim, reason, review = _examine(brain, Config(group), sources, rel)
        if claim is None:
            if reason:
                preview.unclaimed.append(Unclaimed(rel, reason))
            if review:
                preview.review.setdefault(review[0], {})[rel] = {"source_label": review[1], "source_path": review[2]}
            continue
        _, ident, record = claim
        found.setdefault(ident, []).append((rel, record))
    for ident, items in found.items():
        if len(items) > 1:
            preview.unclaimed += [Unclaimed(rel, "two files in raw/ claim the same source file; neither is claimed") for rel, _ in items]
            continue
        rel, record = items[0]
        preview.claims.setdefault(ident[0], {})[rel] = record
    preview.unclaimed.sort(key=lambda u: u.path)
    rebuilt = State(brain)
    for name, files in preview.claims.items():
        rebuilt.pipeline(name).files = dict(files)
    for name, entries in preview.review.items():
        rebuilt.pipeline(name).review = dict(entries)
    now = time.time()
    for pipeline in group:
        others = {collision_key(t): n for n, ps in rebuilt.pipelines.items() if n != pipeline.name for t in ps.files}
        preview.plans[pipeline.name] = plan_pipeline(brain, pipeline, rebuilt.pipeline(pipeline.name), now=now, others=others)
    return preview


def apply_rebuild(brain: Brain, approve: bool = False, trigger: str = "cli", pipeline: str | None = None) -> RebuildPreview:
    config = load_config(brain)
    bound, group = target_group(brain, config, pipeline)
    preview = _rebuild_preview(bound, config, group)
    if preview.needs_approval and not approve:
        raise RebuildRefused("With this state the next sync would delete or overwrite more files than the big-change guard allows "
                             "(see the preview). Check that your source folders are mounted and complete, then repeat with --approve.")
    with brain_lock(brain):
        state = State(bound)
        for name, files in preview.claims.items():
            state.pipeline(name).files = dict(files)
        for name, entries in preview.review.items():
            state.pipeline(name).review = dict(entries)
        set_aside_state(state_home(bound), "state-before-rebuild.json")      # a local target; a repository keeps the old state in its history
        state.save()
        bound.target.commit(f"mdpipeline rebuild state: {preview.claimed_total} files re-owned from provenance")
    preview.applied = True
    ActivityLog(brain).event("rebuild", None, trigger, claimed=preview.claimed_total, unclaimed=len(preview.unclaimed))
    return preview
