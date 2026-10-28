"""The planner: compute the exact change set of a sync, writing nothing (engine-core).

Decision table for a candidate source file S with target T (the pipeline's state records which
brain files it owns, and the hash of the bytes it last wrote):

* T absent                                      -> create
* T exists, no ownership record                 -> conflict (a)  (never overwritten, never forced)
* T owned, brain file differs from last write   -> conflict (b)  (overwritten only with force)
* T owned, clean, source/label/path unchanged   -> unchanged
* T owned, clean, source changed                -> update

A recorded file that no current source file maps to is "source gone": it is deleted (policy
``delete``) or kept (policy ``keep``), but NEVER deleted when its source folder is missing or
unreadable, when its folder label is no longer part of the pipeline, or when the brain copy was
edited (that is conflict (b), and force never deletes).
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Literal

from . import progress
from .boundary import check_target_relpath
from .brain import Brain
from .config import Pipeline
from .frontmatter import FrontMatterError, build_provenance, content_hash, merge_provenance, parse_document
from .guard import GuardState, evaluate_guard
from .naming import ORIGINAL, apply_name_policy, clash_suffix, collision_key, owned_prefixes, policy_label, windows_safe_target
from .sources import SourceEntry, SourceScan, scan_source
from .state import FileRecord, PipelineState, State
from .template import TemplateError, render_target

Action = Literal["create", "update", "rename", "delete", "keep", "skip", "conflict", "unchanged"]
COUNT_KEYS = ("created", "updated", "renamed", "deleted", "kept", "skipped", "conflicts", "unchanged", "errors")
NEXT_STEP = ("To let the pipeline own it again: move your copy out of the output folder if it has edits you want to keep (or delete it; "
             "deleting loses any edits made in your output folder), then sync; the copy is recreated from the source.")
LINKS_NOTE = "Links inside the synced files are not rewritten; update them yourself if needed."


@dataclass
class PlanItem:
    source_label: str
    source_path: str
    target: str
    action: Action
    reason: str = ""
    conflict_type: str | None = None
    error: bool = False
    # execution data
    new_bytes: bytes | None = None
    source_hash: str | None = None
    new_hash: str | None = None
    finalize_pending: bool = False   # unchanged, but state must adopt a pending hash
    # assigned-name data (None for the "original" policy), stored in state when the file is written
    first_seen: str | None = None
    name_policy: str | None = None
    suffix: str | None = None
    from_target: str | None = None   # rename: the owned file that moves to ``target``
    source_version: str | None = None   # MD-45: the connector's change marker of this item (None for folders)
    display: str = ""                   # the item's display path under its source when it differs from the identity (connectors)
    metadata_only: bool = False         # MD-56: an update whose body is unchanged (front-matter/provenance only): not counted by the guard

    def to_dict(self) -> dict:
        d = {"source_label": self.source_label, "source_path": self.source_path, "target": self.target,
             "action": self.action}
        if self.from_target:
            d["from"] = self.from_target
        if self.reason:
            d["reason"] = self.reason
        if self.conflict_type:
            d["conflict_type"] = self.conflict_type
        if self.metadata_only:
            d["metadata_only"] = True
        if self.display:
            d["display"] = self.display
        if self.error:
            d["error"] = True
        return d


def _count(items: list[PlanItem]) -> dict[str, int]:
    c = dict.fromkeys(COUNT_KEYS, 0)
    for i in items:
        if i.error:
            c["errors"] += 1
            continue
        key = {"create": "created", "update": "updated", "rename": "renamed", "delete": "deleted", "keep": "kept", "skip": "skipped",
               "conflict": "conflicts", "unchanged": "unchanged"}[i.action]
        c[key] += 1
    return c


@dataclass
class GroupPlan:
    label: str
    path: str
    status: str = "ok"
    error: str = ""
    items: list[PlanItem] = field(default_factory=list)
    category: str = ""          # a connector failure category (auth, network, rate-limited, ...) when the source is unreadable

    def counts(self) -> dict[str, int]:
        return _count(self.items)


@dataclass
class Plan:
    pipeline: str
    groups: list[GroupPlan]
    guard: GuardState
    owned_total: int
    forgotten: list[str] = field(default_factory=list)   # recorded targets already gone from the brain
    warnings: int = 0                                    # conversion warnings reported by connectors (never content)

    def items(self) -> list[PlanItem]:
        return [i for g in self.groups for i in g.items]

    def counts(self) -> dict[str, int]:
        return _count(self.items())

    @property
    def change_set_id(self) -> str:
        lines = sorted(f"{i.action}|{i.target}|{i.source_hash or ''}" + (f"|{i.from_target}" if i.from_target else "")
                       for i in self.items() if i.action in ("create", "update", "delete", "rename"))
        return "cs-" + hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]

    @property
    def metadata_only(self) -> int:
        """Updates that only change front-matter/provenance (same body). Reported apart; the guard does not count them."""
        return sum(1 for i in self.items() if i.action == "update" and i.metadata_only and not i.error)

    @property
    def errors(self) -> list[dict]:
        out = [{"source": g.label, "error": g.error, **({"category": g.category} if g.category else {})} for g in self.groups if g.status not in ("ok", "off")]
        out += [{"source": i.source_label, "path": i.source_path, "error": i.reason} for i in self.items() if i.error]
        return out

    @property
    def conflicts(self) -> list[dict]:
        return [{"type": i.conflict_type, "path": i.target, "source": i.source_label,
                 "source_path": i.source_path, "reason": i.reason} for i in self.items() if i.action == "conflict"]

    @property
    def notes(self) -> list[str]:
        notes = [LINKS_NOTE] if any(i.action == "rename" for i in self.items()) else []
        scrubbed = sum(1 for i in self.items() if i.reason == SCRUB_REASON and not i.error)
        if scrubbed:
            notes.append(f"{scrubbed} files change only their provenance: the machine path in source_root is replaced by a stable label, because "
                         "this target is a shared repository. It is a one-time change; the file content is not touched.")
        return notes

    def to_dict(self) -> dict:
        return {"pipeline": self.pipeline, "counts": self.counts(), "metadata_only": self.metadata_only, "change_set_id": self.change_set_id,
                "guard": self.guard.to_dict(), "notes": self.notes,
                "groups": [{"label": g.label, "path": g.path, "status": g.status, "error": g.error,
                            "counts": g.counts(), "items": [i.to_dict() for i in g.items if i.action != "unchanged"]} for g in self.groups]}


def _brain_file_hash(brain: Brain, target: str) -> tuple[str, str | None]:
    """Return (kind, hash): kind is 'absent', 'file' or 'other' (symlink, directory...). Read through the brain's target;
    a target that cannot be read raises ``TargetError`` (never 'absent')."""
    read = brain.target.read_file(target)
    if read.kind != "file":
        return read.kind, None
    return "file", content_hash(read.data or b"")


def _effective_written(rec: FileRecord, current_hash: str | None) -> str | None:
    """The recorded hash the brain file currently matches, else None (= edited in the brain)."""
    if current_hash is not None and current_hash == rec.written_hash:
        return rec.written_hash
    if current_hash is not None and rec.pending_hash and current_hash == rec.pending_hash:
        return rec.pending_hash
    return None


def default_scans(pipeline: Pipeline, ps: PipelineState | None = None) -> list[SourceScan]:
    """Scan every source of the pipeline through the source contract (an adapter that fails is an unreadable source)."""
    scans = []
    for source in pipeline.sources:
        known = {r.source_path: r.source_version for r in (ps.files.values() if ps else [])
                 if r.source_label == source.label and r.source_version}
        scans.append(scan_source(source, pipeline.include, pipeline.exclude, known))
    return scans


BODY_READ_WORKERS = 8      # parallel body fetches for a connector source (the Jira API client keeps no per-call state)


def _prefetch_bodies(scans: list[SourceScan], ps: PipelineState, by_identity: dict[tuple[str, str], list[str]]) -> None:
    """MD-153: fetch the bodies a connector source will need together instead of one after another (210 issues: ~106 s -> seconds).

    Only items whose version changed (or that are new) are fetched; a failed fetch is kept and re-raised when the planner reads
    that item, so one bad item still fails alone. Reports "reading N of M" for the first-run screen."""
    from concurrent.futures import ThreadPoolExecutor

    todo = []
    for scan in scans:
        for entry in scan.entries:
            if entry.status != "candidate" or not callable(entry.content) or not entry.version:
                continue
            if any(ps.files[t].source_version == entry.version for t in by_identity.get((scan.label, entry.identity), [])):
                continue          # the connector says nothing changed: the planner does not fetch it either
            todo.append(entry)
    if len(todo) < 2:
        return
    progress.report("reading", 0, len(todo))
    done = {"n": 0}
    lock = threading.Lock()

    def fetch(entry: SourceEntry) -> None:
        try:
            data = entry.content()
            entry.content = data
        except Exception as exc:        # noqa: BLE001 - re-raised by read_bytes() for this item only
            def failed(exc=exc) -> bytes:
                raise exc
            entry.content = failed
        with lock:
            done["n"] += 1
            progress.report("reading", done["n"])

    with ThreadPoolExecutor(max_workers=BODY_READ_WORKERS) as pool:
        list(pool.map(fetch, todo))


def plan_pipeline(brain: Brain, pipeline: Pipeline, ps: PipelineState, *, now: float, force: bool = False,
                  scans: list[SourceScan] | None = None, force_paths: frozenset[str] | None = None,
                  others: dict[str, str] | None = None, scrub_paths: bool = False, shallow: bool = False) -> Plan:
    """Plan one pipeline. Reads sources and the brain; writes nothing.

    ``shallow`` (MD-153, the first-run Review only): a NEW item of a connector source (one that reports a version) is planned as a
    ``create`` without fetching its body, so reviewing 200 issues costs the listing, not 400 requests. A shallow plan carries no
    content or hashes and must never be applied.

    ``scrub_paths`` (MD-72): also plan the one-time rewrite of owned files whose provenance still carries a machine path.

    ``others`` maps the collision key of every target owned by the brain's OTHER pipelines to its owner; by
    default it is read from the saved state."""
    scans = scans if scans is not None else default_scans(pipeline, ps)
    source_by_label = {s.label: s for s in pipeline.sources}
    groups = [GroupPlan(s.label, s.path, s.status, s.error, category=s.category) for s in scans]
    group_by_label = {g.label: g for g in groups}
    produced: set[str] = set()       # targets of present candidate files
    errored: set[tuple[str, str]] = set()   # candidates that failed: their brain copy is never deleted
    claimed: dict[str, str] = {}     # casefolded target -> "label:relpath"
    by_identity: dict[tuple[str, str], list[str]] = {}   # (label, relpath) -> recorded targets
    for recorded_target, rec in ps.files.items():
        by_identity.setdefault((rec.source_label, rec.source_path), []).append(recorded_target)
    if others is None:
        others = _owned_elsewhere(brain, pipeline.name)
    if not shallow:
        _prefetch_bodies(scans, ps, by_identity)
    pairs = _pair_pure_renames(pipeline, ps, scans, by_identity)
    brain.target.prefetch(sorted(ps.files))      # MD-128: a repository target downloads the owned files together, not one request per file
    names = _assign_names(brain, pipeline, ps, scans, by_identity, others, now, pairs)
    owned = owned_prefixes([*ps.files, *ps.review])     # MD-182 AC2: names the brain already holds are never changed

    for scan, group in zip(scans, groups):
        for entry in scan.entries:
            if entry.status == "skip":
                group.items.append(PlanItem(scan.label, entry.identity, "", "skip", entry.reason, display=entry.relpath if entry.relpath != entry.identity else ""))
                continue
            planned = _plan_candidate(brain, pipeline, ps, scan, entry, now, force, produced, claimed, force_paths, names, by_identity, pairs, scrub_paths, shallow, owned)
            if entry.relpath != entry.identity:
                planned.display = entry.relpath
            if planned.error:
                errored.add((scan.label, entry.identity))
            group.items.append(planned)

    # recorded files whose source file is gone
    current_policy = policy_label(pipeline.name_policy, pipeline.name_pattern) if pipeline.name_policy != ORIGINAL else ORIGINAL
    present = {(i.source_label, i.source_path): i.target for g in groups for i in g.items
               if i.target and not i.error and i.action in ("create", "update", "rename", "unchanged")}
    forgotten: list[str] = []
    for target, rec in sorted(ps.files.items()):
        if target in produced:
            continue
        if (rec.source_label, rec.source_path) in errored:
            continue  # the file exists but failed (error): never treat it as deleted
        group = group_by_label.get(rec.source_label)
        label_known = rec.source_label in source_by_label
        if group is None:
            group = group_by_label.setdefault(rec.source_label, GroupPlan(rec.source_label, "", "ok"))
            if group not in groups:
                groups.append(group)
        superseded = (rec.name_policy or ORIGINAL) != current_policy and present.get((rec.source_label, rec.source_path), target) != target
        item = _plan_gone(brain, pipeline, rec, target, group, label_known, forgotten, force, superseded)
        if item is not None:
            group.items.append(item)

    owned_total = len(ps.files)
    count = sum(1 for g in groups for i in g.items
                if i.action in ("delete", "update", "rename") and not i.error and not (i.action == "update" and i.metadata_only))
    guard = evaluate_guard(count, owned_total, pipeline.guard_threshold_percent, pipeline.guard_min_files)
    deleting = {i.source_label for g in groups for i in g.items if i.action == "delete" and not i.error}
    emptied = [scan for scan in scans if scan.guard_empty and scan.status == "ok" and scan.label in deleting
               and not any(e.status == "candidate" for e in scan.entries)]
    if emptied and not guard.blocked:      # a connector that suddenly returns nothing is suspicious: the guard always asks
        owned = sum(1 for r in ps.files.values() if r.source_label == emptied[0].label)
        guard = GuardState(True, count, owned_total, (count / owned_total * 100.0) if owned_total else 0.0, pipeline.guard_threshold_percent,
                           pipeline.guard_min_files,
                           f"The source '{emptied[0].label}' returned 0 items, but this pipeline owns {owned} files from it. This may be a wrong "
                           "query or lost access, so nothing is deleted until you approve (in the UI or with --approve).")
    return Plan(pipeline.name, groups, guard, owned_total, forgotten, sum(scan.warnings for scan in scans))


@dataclass
class _Assigned:
    target: str
    first_seen: str | None = None
    name_policy: str | None = None
    suffix: str | None = None

    def provenance_names(self, relpath: str) -> dict | None:
        if self.name_policy is None:
            return None
        return {"original_name": posixpath.basename(relpath), "first_seen": self.first_seen,
                "name_policy": self.name_policy, "name_suffix": self.suffix}


def _owned_elsewhere(brain: Brain, name: str) -> dict[str, str]:
    state = State.load(brain)
    return {collision_key(t): f"{pname}" for pname, other in state.pipelines.items() if pname != name for t in other.files}


def _recorded_first_seen(brain: Brain, targets: list[str], ps: PipelineState, fallback: str) -> str:
    """First-seen date of a source file that already has records: the stored date, else (a record made before name
    policies existed) the date in the provenance of the brain copy, else ``fallback``."""
    for t in targets:
        rec = ps.files[t]
        if rec.first_seen:
            return rec.first_seen
    for t in targets:
        try:
            read = brain.target.read_file(t)
            if read.kind != "file":
                continue
            meta, _ = parse_document((read.data or b"").decode("utf-8"))
            synced = str((meta.get("provenance") or {}).get("synced_at", ""))[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", synced):
                return synced
        except (UnicodeDecodeError, AttributeError):
            continue
    return fallback


def _pair_pure_renames(pipeline: Pipeline, ps: PipelineState, scans: list[SourceScan], by_identity: dict
                       ) -> dict[tuple[str, str], str]:
    """MD-35 AC4: new source files whose content equals that of exactly one source file that vanished in this
    pipeline, in this sync. Returns {new identity: old target}. Ambiguous matches are never paired."""
    if pipeline.name_policy == ORIGINAL:
        return {}
    plabel = policy_label(pipeline.name_policy, pipeline.name_pattern)
    ok_labels = {scan.label for scan in scans if scan.status == "ok"}
    present = {(scan.label, e.identity) for scan in scans for e in scan.entries if e.status == "candidate"}
    vanished: dict[str, list[str]] = {}
    for target, rec in ps.files.items():
        if ((rec.source_label, rec.source_path) in present or rec.source_label not in ok_labels or rec.name_policy != plabel
                or not rec.source_hash):
            continue
        vanished.setdefault(rec.source_hash, []).append(target)
    if not vanished:
        return {}
    appeared: dict[str, list[tuple[str, str]]] = {}
    for scan in scans:
        for entry in scan.entries:
            ident = (scan.label, entry.identity)
            if entry.status != "candidate" or ident in by_identity:
                continue
            try:
                digest = content_hash(entry.read_bytes())
            except OSError:
                continue
            if digest in vanished:
                appeared.setdefault(digest, []).append(ident)
    return {idents[0]: vanished[digest][0] for digest, idents in appeared.items()
            if len(idents) == 1 and len(vanished[digest]) == 1}


def _assign_names(brain: Brain, pipeline: Pipeline, ps: PipelineState, scans: list[SourceScan], by_identity: dict,
                  others: dict[str, str], now: float, pairs: dict | None = None) -> dict[tuple[str, str], _Assigned]:
    """Decide the target of every candidate file at once (MD-35, MD-36, MD-37).

    A recorded file keeps its name and first-seen date (only the directory follows the template). A new file, or
    one whose policy changed, gets the clean name unless it is taken: by another pipeline, by a recorded file, or
    by a new file whose source identity sorts first. The loser gets ``<sep><6 hex>`` from its identity; the
    suffix is then recorded and kept."""
    if pipeline.name_policy == ORIGINAL:
        return {}
    owned = owned_prefixes([*ps.files, *ps.review])
    plabel = policy_label(pipeline.name_policy, pipeline.name_pattern)
    today = datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()
    taken: dict[str, tuple[str, str]] = {}
    templated: dict[tuple[str, str], str] = {}
    shown: dict[tuple[str, str], SourceEntry] = {}     # identity -> the entry (its display path and created date)
    for scan in scans:
        for entry in scan.entries:
            if entry.status != "candidate" or not scan.label or scan.label in (".", "..") or any(c in scan.label for c in "/\\\x00"):
                continue
            try:
                templated[(scan.label, entry.identity)] = windows_safe_target(render_target(
                    pipeline.target, pipeline=pipeline.name, source=scan.label, relpath=entry.relpath, mtime=entry.mtime), owned)
                shown[(scan.label, entry.identity)] = entry
            except TemplateError:
                continue
    out: dict[tuple[str, str], _Assigned] = {}
    reviewed = {(v["source_label"], v["source_path"]): path for path, v in ps.review.items()}
    for recorded, rec in ps.files.items():     # everything already recorded keeps its place in the name space
        taken.setdefault(collision_key(recorded), (rec.source_label, rec.source_path))
    for ident, template_target in sorted(templated.items()):
        keep = next((t for t in by_identity.get(ident, []) if ps.files[t].name_policy == plabel), None)
        if keep is not None:
            rec = ps.files[keep]
            directory, name = posixpath.dirname(template_target), posixpath.basename(keep)
            target = posixpath.join(directory, name) if directory else name
            out[ident] = _Assigned(target, rec.first_seen or today, plabel, rec.suffix)
            taken[collision_key(target)] = ident
    for ident, template_target in sorted(templated.items()):
        if ident in out:
            continue
        label, key = ident
        rel = shown[ident].relpath                   # the display path feeds the name; the identity feeds the clash suffix
        if ident in reviewed and ident not in by_identity:      # a rebuild left this copy unowned: keep its name
            out[ident] = _Assigned(reviewed[ident], today, plabel, None)
            taken[collision_key(reviewed[ident])] = ident
            continue
        paired = [pairs[ident]] if pairs and ident in pairs else []
        created = shown[ident].created
        first_seen = _recorded_first_seen(brain, by_identity.get(ident, []) + paired, ps,
                                          datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat() if created is not None else today)
        old_ident = ((ps.files[paired[0]].source_label, ps.files[paired[0]].source_path)) if paired else None
        try:
            clean = windows_safe_target(apply_name_policy(template_target, pipeline.name_policy, pipeline.name_pattern, relpath=rel,
                                                          first_seen=first_seen, source=label, pipeline=pipeline.name), owned)
        except KeyError:
            continue
        suffix, target = None, clean
        length = 6
        while collision_key(target) in others or taken.get(collision_key(target), ident) not in (ident, old_ident):
            suffix = clash_suffix(label, key, length)
            target = windows_safe_target(apply_name_policy(template_target, pipeline.name_policy, pipeline.name_pattern, relpath=rel,
                                                           first_seen=first_seen, source=label, pipeline=pipeline.name, suffix=suffix), owned)
            length += 2
            if length > 64:
                break
        out[ident] = _Assigned(target, first_seen, plabel, suffix)
        taken[collision_key(target)] = ident
    return out


def _plan_candidate(brain, pipeline, ps, scan, entry: SourceEntry, now, force, produced, claimed, force_paths=None,
                    names=None, by_identity=None, pairs=None, scrub_paths=False, shallow=False, owned=frozenset()) -> PlanItem:
    label, rel = scan.label, entry.identity      # identity: stable (ownership, names, provenance); entry.relpath is the display path

    def skip_error(reason: str, target: str = "") -> PlanItem:
        return PlanItem(label, rel, target, "skip", reason, error=True)

    if not label or label in (".", "..") or any(c in label for c in "/\\\x00"):
        return skip_error(f"refused: invalid source label {label!r}")
    try:
        target = render_target(pipeline.target, pipeline=pipeline.name, source=label, relpath=entry.relpath, mtime=entry.mtime)
        assigned = (names or {}).get((label, rel)) or _Assigned(target)
        target = windows_safe_target(assigned.target, owned)        # MD-182 AC2: CON, NUL, AUX... get one underscore, on every OS
    except (TemplateError, KeyError) as exc:
        return skip_error(f"invalid target template or file-name policy: {exc}")
    refused = check_target_relpath(target)
    if refused is None and (not target.endswith(".md") or target.rsplit("/", 1)[-1] == ".md"):
        refused = "only Markdown (.md) files may be written"
    if refused:
        return skip_error(f"refused: {refused}", target)
    key = collision_key(target)
    if key in claimed:
        return skip_error(f"target collision with {claimed[key]}", target)
    claimed[key] = f"{label}:{rel}"
    produced.add(target)
    current_policy = ORIGINAL if pipeline.name_policy == ORIGINAL else policy_label(pipeline.name_policy, pipeline.name_pattern)
    known = ps.files.get(target)
    if (entry.version and known is not None and known.source_version == entry.version and known.source_label == label
            and known.source_path == rel and (known.name_policy or ORIGINAL) == current_policy and known.source_hash):
        kind0, current0 = _brain_file_hash(brain, target)     # the connector says nothing changed: do not fetch the content
        effective0 = _effective_written(known, current0) if kind0 == "file" else None
        if effective0 is not None:
            return PlanItem(label, rel, target, "unchanged", "", None, False, None, known.source_hash, known.written_hash or known.pending_hash,
                            effective0 != known.written_hash, assigned.first_seen, assigned.name_policy, assigned.suffix,
                            source_version=entry.version)
    if shallow and known is None and entry.version and not by_identity.get((label, rel)) and _brain_file_hash(brain, target)[0] == "absent":
        return PlanItem(label, rel, target, "create", "", None, False, None, "", "", False, assigned.first_seen, assigned.name_policy, assigned.suffix,
                        source_version=entry.version)
    try:
        data = entry.read_bytes()
    except OSError as exc:
        return skip_error(f"cannot read source file: {exc.strerror or exc}", target)
    except Exception as exc:        # a connector item that cannot be fetched or converted fails alone (never read as deleted)
        return skip_error(f"cannot read source item: {exc}", target)
    src_hash = content_hash(data)
    prov = build_provenance(pipeline=pipeline.name, source_label=label, source_path=rel, source_root=shown_root(brain, scan),
                            source_mtime=entry.mtime, synced_at=now, source_bytes=data, names=assigned.provenance_names(entry.relpath),
                            source_type="local" if scan.kind == "folder" else scan.kind, extra=entry.provenance or None)
    try:
        merged = merge_provenance(data, prov)
    except FrontMatterError as exc:
        return skip_error(f"front-matter error: {exc}", target)
    new_hash = content_hash(merged)

    def item(action: Action, reason: str = "", conflict: str | None = None, finalize: bool = False) -> PlanItem:
        return PlanItem(label, rel, target, action, reason, conflict, False,
                        merged if action in ("create", "update", "rename") else None, src_hash, new_hash, finalize,
                        assigned.first_seen, assigned.name_policy, assigned.suffix, source_version=entry.version or None)

    kind, current = _brain_file_hash(brain, target)
    rec = ps.files.get(target)
    if kind == "other":
        return skip_error("the target exists and is not a regular file", target)
    moving = next((t for t in (by_identity or {}).get((label, rel), [])
                   if t != target and ((ps.files[t].name_policy or ORIGINAL) != current_policy or scan.kind != "folder")), None) if rec is None else None
    if moving is None and rec is None and pairs and (label, rel) in pairs:
        moving = pairs[(label, rel)]       # MD-35 AC4: the same content vanished elsewhere in this pipeline
    if moving is not None:       # MD-37: the policy changed, so the owned file moves to its new name
        old_rec = ps.files[moving]
        old_kind, old_current = _brain_file_hash(brain, moving)
        if old_kind == "other":
            return skip_error("the file to rename is not a regular file", moving)
        if old_kind == "file":
            produced.add(moving)
            if kind == "file":
                return item("conflict", "the new name is taken by a file this pipeline does not own; not renamed", "a")
            if _effective_written(old_rec, old_current) is None and not (force or (force_paths and moving in force_paths)):
                return PlanItem(label, rel, moving, "conflict", "owned file was edited in your output folder; not renamed", "b")
            renamed = item("rename", f"renamed from {moving}")
            renamed.from_target = moving
            return renamed
    if kind == "absent":
        return item("create", "missing in your output folder, recreated" if rec else "")
    if rec is None or (rec.written_hash is None and rec.pending_hash != current):
        if _claims_to_be_ours(brain, target, pipeline.name, label, rel):
            return item("conflict", "target exists and is not owned by this pipeline: its provenance says it was copied from this "
                                    "source, but cannot tell whether the source or the copy in your output folder changed. " + NEXT_STEP, "a")
        return item("conflict", "target exists and is not owned by this pipeline", "a")
    effective = _effective_written(rec, current)
    if effective is None:
        if force or (force_paths and target in force_paths):
            return item("update", "forced over an edit made in your output folder")
        return item("conflict", "owned file was edited in your output folder since the last sync", "b")
    if (rec.source_hash == src_hash and rec.source_label == label and rec.source_path == rel
            and (rec.name_policy or ORIGINAL) == current_policy):
        if scrub_paths and _leaks_machine_path(brain, target, scan):
            leak = item("update", SCRUB_REASON)
            leak.metadata_only = _same_body(brain, target, merged)
            return leak
        return item("unchanged", finalize=(effective != rec.written_hash))     # a new version with the same bytes is adopted by the engine
    updated = item("update", "" if (rec.name_policy or ORIGINAL) == current_policy else "file-name policy changed")
    updated.metadata_only = _same_body(brain, target, merged)
    return updated


SCRUB_REASON = "provenance: the machine path in source_root is replaced by a stable label (MD-72)"


def shown_root(brain: Brain, scan: SourceScan) -> str:
    """The ``source_root`` a file's provenance shows. A repository is shared, so for a folder source it gets the stable label
    (the same on every machine) instead of the absolute path of this machine (MD-72); everything else keeps what it had."""
    if scan.kind == "folder" and getattr(brain.target, "kind", "") == "github":
        return f"local:{scan.label}"
    return scan.path


def _leaks_machine_path(brain: Brain, target: str, scan: SourceScan) -> bool:
    """The owned file at ``target`` still shows an absolute machine path as its source_root (a GitHub target, folder source)."""
    if scan.kind != "folder" or getattr(brain.target, "kind", "") != "github":
        return False
    try:
        read = brain.target.read_file(target)
        root = (parse_document((read.data or b"").decode("utf-8"))[0].get("provenance") or {}).get("source_root", "") if read.kind == "file" else ""
    except (UnicodeDecodeError, AttributeError):
        return False
    root = str(root)
    return root.startswith(("/", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", root))


def _same_body(brain: Brain, target: str, new: bytes) -> bool:
    """MD-56: the file at ``target`` and the bytes about to replace it differ only in front-matter/provenance."""
    try:
        read = brain.target.read_file(target)
        if read.kind != "file":
            return False
        return parse_document((read.data or b"").decode("utf-8"))[1] == parse_document(new.decode("utf-8"))[1]
    except UnicodeDecodeError:
        return False


def _claims_to_be_ours(brain: Brain, target: str, pipeline: str, label: str, rel: str) -> bool:
    try:
        read = brain.target.read_file(target)
        if read.kind != "file":
            return False
        meta, _ = parse_document((read.data or b"").decode("utf-8"))
    except UnicodeDecodeError:
        return False
    prov = meta.get("provenance")
    return isinstance(prov, dict) and (prov.get("pipeline"), prov.get("source_label"), prov.get("source_path")) == (pipeline, label, rel)


def _plan_gone(brain, pipeline, rec: FileRecord, target, group: GroupPlan, label_known: bool,
               forgotten: list[str], force: bool, superseded: bool = False) -> PlanItem | None:
    label, rel = rec.source_label, rec.source_path

    def mk(action: Action, reason: str, conflict: str | None = None, error: bool = False) -> PlanItem:
        return PlanItem(label, rel, target, action, reason, conflict, error)

    if group.status == "off":
        return mk("keep", "source off: kept")
    if group.status != "ok":
        return mk("keep", f"source folder {group.status}; nothing deleted")
    if not label_known:
        return mk("keep", f"source folder '{label}' is no longer part of the pipeline; file kept")
    if pipeline.on_source_delete == "keep" and not superseded:
        return mk("keep", "source deleted")
    kind, current = _brain_file_hash(brain, target)
    if kind == "absent":
        forgotten.append(target)
        return None
    if kind == "other":
        return mk("skip", "the target is not a regular file", error=True)
    if _effective_written(rec, current) is None:
        return mk("conflict", "source deleted but the file was edited in your output folder; not deleted", "b")
    if superseded:
        return mk("delete", "superseded: the file now lives under its new name")
    return mk("delete", "source file deleted, moved or no longer matches the rules")
