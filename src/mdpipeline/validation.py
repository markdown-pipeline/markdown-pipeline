"""Save-time validation of a pipeline (shared by CLI and UI, so messages are identical)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .boundary import PROTECTED_FOLDERS, check_target_relpath
from .brain import Brain
from .config import ON_DELETE_VALUES, Config, Pipeline
from .destinations import conflicting_pipelines, credential_conflict_message, destination_key, destination_type, directory_root
from .naming import apply_name_policy, check_name_policy, collision_key
from .sources import SourceScan, scan_source
from .template import TemplateError, render_target

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass
class Issue:
    code: str
    message: str
    field: str = ""  # which form field or row the issue belongs to (for the UI)


@dataclass
class ValidationResult:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    file_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def can_save_and_sync(self) -> bool:
        return self.ok and self.file_count > 0


def scan_pipeline(pipeline: Pipeline) -> list[SourceScan]:
    """Scan the pipeline's folder sources (connector sources are checked by their own validation, not listed here)."""
    return [scan_source(s, pipeline.include, pipeline.exclude) for s in pipeline.sources if s.type == "folder"]


def target_for(pipeline: Pipeline, label: str, relpath: str, mtime: float, first_seen: str = "2000-01-01") -> str:
    """Template target with the file-name policy applied (``first_seen`` only matters for date policies)."""
    target = render_target(pipeline.target, pipeline=pipeline.name, source=label, relpath=relpath, mtime=mtime)
    return apply_name_policy(target, pipeline.name_policy, pipeline.name_pattern, relpath=relpath,
                             first_seen=first_seen, source=label, pipeline=pipeline.name)


def _real(path: str) -> str:
    return os.path.realpath(path)


def _is_within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def validate_pipeline(brain: Brain, config: Config, pipeline: Pipeline, editing: str | None = None, remote: bool = False) -> ValidationResult:
    """Validate ``pipeline`` against ``config`` (the saved pipelines). ``editing`` is the saved name being edited.

    ``remote``: also check connector sources against their service (site reachable, credentials accepted, query valid). Saving
    passes True; the live editor preview does not (it must not call the network on every keystroke)."""
    res = ValidationResult()
    err = lambda code, msg, fld="": res.errors.append(Issue(code, msg, fld))  # noqa: E731
    warn = lambda code, msg, fld="": res.warnings.append(Issue(code, msg, fld))  # noqa: E731
    others = [p for p in config.pipelines if p.name != editing]

    # --- name, policy, guard, sources present
    if not NAME_RE.match(pipeline.name or ""):
        err("bad-name", f"Invalid pipeline name {pipeline.name!r}: use letters, digits, '.', '_' or '-' (max 64, no spaces or slashes).", "name")
    elif any(p.name == pipeline.name for p in others):
        err("duplicate-name", f"A pipeline named '{pipeline.name}' already exists.", "name")
    if pipeline.on_source_delete not in ON_DELETE_VALUES:
        err("bad-policy", f"on_source_delete must be 'delete' or 'keep', not {pipeline.on_source_delete!r}.", "on_source_delete")
    if not (0 <= pipeline.guard_threshold_percent <= 100) or pipeline.guard_min_files < 0:
        err("bad-guard", "Guard threshold must be between 0 and 100 percent and the minimum file count must not be negative.", "guard")
    for message in check_name_policy(pipeline.name_policy, pipeline.name_pattern):
        err("bad-name-policy", message, "name_policy")
    if not pipeline.sources:
        err("no-sources", "Add at least one source.", "sources")
    elif not any(s.enabled for s in pipeline.sources):
        err("all-sources-off", "Turn a source on, or pause the pipeline.", "sources")

    # --- labels
    seen_labels: set[str] = set()
    for i, src in enumerate(pipeline.sources):
        fld = f"sources[{i}].label"
        if not src.label or src.label in (".", "..") or "/" in src.label or "\\" in src.label or "\x00" in src.label:
            err("bad-label", f"Source label {src.label!r} is not allowed: it must be non-empty and contain no '/' or '\\' and not be '.' or '..'.", fld)
        elif src.label in seen_labels:
            err("duplicate-label", f"Source label '{src.label}' is used more than once in this pipeline.", fld)
        seen_labels.add(src.label)

    # --- the target (MD-78): a folder that exists, or a well-formed repository name (the live check is the caller's)
    target_root = _check_destination(brain, pipeline, err, editing is not None)

    clash = conflicting_pipelines(brain, pipeline, others)
    if clash:
        err("credential-conflict", credential_conflict_message(pipeline, clash), "destination.credential")

    # --- connector sources (Jira): static checks, and with ``remote`` the service itself
    from .jira_source import check_jira_source

    saved = next((p for p in config.pipelines if p.name == editing), None) if editing else None
    if remote and destination_type(pipeline.destination) == "github" and (pipeline.destination or {}).get("credential") \
            and not any(i.code in ("bad-repo", "bad-branch") for i in res.errors) \
            and not (saved and saved.destination == pipeline.destination):          # an untouched GitHub target is not re-checked on every edit
        from .target import TargetError
        from .targetconfig import GitHubSpec, validate_github_spec

        try:
            gh_result, _ = validate_github_spec(GitHubSpec.from_dict(pipeline.destination or {}))
            res.errors.extend(gh_result.errors)
        except TargetError as exc:                 # e.g. a locked vault: the save stops, nothing is written
            err("github-" + exc.category, str(exc), "destination")
    for i, src in enumerate(pipeline.sources):
        if src.type == "jira":
            unchanged = bool(saved and any(o.type == "jira" and o.label == src.label and o.options == src.options for o in saved.sources))
            found, matches = check_jira_source(src, remote and not unchanged and src.enabled)      # untouched or Off: no request
            for code, message in found:
                err(code, message, f"sources[{i}]")
            if src.enabled:
                res.file_count += matches if matches is not None else (0 if found else 1)

    # --- source folders: type, duplicates, nesting, brain relation
    brain_real = _real(str(brain.root))
    written_real = _written_area(brain_real, pipeline.target)
    reals = []
    brain_bad: set[int] = set()   # rows already rejected for their relation to the brain (checked first)
    for i, src in enumerate(pipeline.sources):
        if src.type != "folder":
            reals.append("")      # connector sources have no folder: they take no part in the folder checks below
            brain_bad.add(i)
            continue
        fld = f"sources[{i}].path"
        real = _real(src.path)
        reals.append(real)
        if os.path.exists(src.path) and not os.path.isdir(src.path):
            err("source-not-a-folder", f"{src.path} is not a folder.", fld)
        if _in_written_area(real, brain_real, written_real):
            brain_bad.add(i)
            err("source-in-output", f"{src.path} is inside the folder the files are written to ({_shown(written_real)}); copying a folder into itself would loop.", fld)
        if _is_within(brain_real, real):
            brain_bad.add(i)
            err("source-contains-brain", f"{src.path} contains your output folder ({brain.root}); a source must not contain it.", fld)
    if target_root is not None:
        target_real = _real(str(target_root))
        target_written = _written_area(target_real, pipeline.target)
        for i, src in enumerate(pipeline.sources):
            if src.type != "folder" or i in brain_bad:
                continue
            if _in_written_area(reals[i], target_real, target_written):
                brain_bad.add(i)
                err("source-in-output", f"{src.path} is inside the folder the files are written to ({_shown(target_written)}); copying a folder into itself would loop.", f"sources[{i}].path")
            elif _is_within(target_real, reals[i]):
                brain_bad.add(i)
                err("source-contains-target", f"{src.path} contains the target folder ({target_root}); a source must not contain the target.", f"sources[{i}].path")
    for i in range(len(reals)):
        for j in range(i + 1, len(reals)):
            if i in brain_bad or j in brain_bad:
                continue
            _compare_sources(err, reals[i], reals[j], pipeline.sources[i].path, pipeline.sources[j].path, "this pipeline", f"sources[{j}].path")
    for other in others:
        for j, src in enumerate(pipeline.sources):
            if j in brain_bad:
                continue
            for osrc in (o for o in other.sources if o.type == "folder"):
                _compare_sources(err, reals[j], _real(osrc.path), src.path, osrc.path, f"pipeline '{other.name}'", f"sources[{j}].path")

    # --- template (static, with placeholder values)
    template_ok = not any(i.code == "bad-name-policy" for i in res.errors)
    try:
        sample = target_for(pipeline, "label", "dir/file.md", 0.0) if template_ok else ""
        reason = check_target_relpath(sample)
        if pipeline.target.startswith("/"):
            reason = "the target path is absolute"
        if reason and template_ok:
            template_ok = False
            err("target-not-allowed", f"Target template {pipeline.target!r} is not allowed: {reason}.", "target")
    except TemplateError as exc:
        template_ok = False
        err("bad-template", str(exc), "target")

    # --- real files: boundary per file, collisions, missing folders, file count
    if template_ok and not any(i.code in ("bad-label", "duplicate-label") for i in res.errors):
        claimed: dict[str, str] = {}
        mine = destination_key(brain, pipeline)
        for other in (o for o in others if destination_key(brain, o) == mine):      # only pipelines that share this target can clash
            if any(i.code == "bad-template" for i in res.errors):
                break
            for scan in scan_pipeline(other):
                for e in scan.entries:
                    if e.status == "candidate":
                        try:
                            t = target_for(other, scan.label, e.relpath, e.mtime)
                        except (TemplateError, KeyError):
                            continue
                        claimed.setdefault(collision_key(t), f"{other.name}/{scan.label}:{e.relpath}")
        reported: set[str] = set()
        for scan in scan_pipeline(pipeline):
            if scan.status == "missing":
                warn("source-missing", f"Source folder not found: {scan.path} (e.g. an unmounted drive). Saving is allowed; nothing will be deleted because of it.", "sources")
            elif scan.status == "unreadable":
                warn("source-unreadable", f"Source folder cannot be read: {scan.path}.", "sources")
            for e in scan.entries:
                if e.status != "candidate":
                    continue
                res.file_count += 1
                target = target_for(pipeline, scan.label, e.relpath, e.mtime)
                reason = check_target_relpath(target)
                if reason and "boundary" not in reported:
                    reported.add("boundary")
                    err("target-not-allowed", f"{scan.label}:{e.relpath} would land at {target!r}: {reason}.", "target")
                    continue
                key, me = collision_key(target), f"{pipeline.name}/{scan.label}:{e.relpath}"
                if key in claimed:
                    if pipeline.name_policy != "original":
                        continue   # a clash is resolved by the planner with a stable hash suffix (MD-36)
                    err("target-collision", f"{me} and {claimed[key]} both resolve to {target}.", "target")
                else:
                    claimed[key] = me
        if res.file_count == 0 and res.ok:
            warn("no-files", "No files match the include and exclude rules. You can save, but 'Save & sync' is not offered.", "include")
    return res


def _written_area(root_real: str, template: str) -> str:
    """The folder a pipeline's files land in, as far as the template says: the folders before its first variable (MD-133).
    ``raw/{pipeline}/{relpath}`` -> ``<root>/raw``; ``{relpath}`` -> ``<root>`` itself."""
    parts: list[str] = []
    for seg in (template or "").strip("/").split("/")[:-1]:
        if "{" in seg or seg in ("", ".", ".."):
            break
        parts.append(seg)
    return os.path.join(root_real, *parts)


def _in_written_area(source_real: str, root_real: str, written_real: str) -> bool:
    """A source inside the written area would be copied into itself. The app's own folder and wiki/ are never written, so a
    source there is no loop."""
    if not _is_within(source_real, written_real):
        return False
    if written_real == root_real:
        first = os.path.relpath(source_real, root_real).split(os.sep)[0].casefold()
        return first not in PROTECTED_FOLDERS
    return True


def _shown(path: str) -> str:
    return path


def _check_destination(brain: Brain, pipeline: Pipeline, err, editing: bool = False):
    """Static checks of the pipeline's target. Returns the folder of a directory target (None otherwise)."""
    dest = pipeline.destination
    kind = destination_type(dest)
    if kind == "directory":
        root = directory_root(brain, dest)
        if not root.is_dir():
            err("target-not-a-folder", f"The target folder {root} does not exist or is not a folder. Create it first.", "destination")
            return None
        return None if root.resolve() == brain.root.resolve() else root
    if kind == "github":
        from .targetconfig import BRANCH_RE, REPO_RE

        if not REPO_RE.match((dest or {}).get("repo", "")):
            err("bad-repo", f"'{(dest or {}).get('repo', '')}' is not a repository name; use OWNER/REPO.", "destination.repo")
        if (dest or {}).get("branch") and not BRANCH_RE.match(dest["branch"]):
            err("bad-branch", f"'{dest['branch']}' is not a usable branch name.", "destination.branch")
        if not (dest or {}).get("credential") and not editing:
            err("no-credential", "A GitHub target needs a token or a sign-in: enter a token (it is kept in the app vault) or sign in with GitHub.", "destination.credential")
        return None
    err("bad-target-type", f"Unknown target type '{kind}': use a directory or a GitHub repository.", "destination")
    return None


def _compare_sources(err, a: str, b: str, a_shown: str, b_shown: str, where: str, fld: str) -> None:
    if a == b:
        err("duplicate-source", f"The folder {b_shown} is already a source of {where}.", fld)
    elif _is_within(a, b) or _is_within(b, a):
        err("nested-source", f"{a_shown} and {b_shown} are nested (one is inside the other); see {where}.", fld)
