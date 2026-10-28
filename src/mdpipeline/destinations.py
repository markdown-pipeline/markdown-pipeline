"""Where a pipeline's files land (MD-78): the pipeline's own destination, turned into a target.

A pipeline has no destination (its files land in the brain's own folder, ``raw/`` there), a ``directory`` destination
(another folder with its own ``raw/`` and state) or a ``github`` destination (a repository branch). ``bind_target`` returns
a ``Brain`` *view*: the same control directory (configuration, activity log, lock) with that pipeline's target, so the
engine, the planner and the state work per pipeline without knowing about destinations.
"""
from __future__ import annotations

import json
from pathlib import Path

from .brain import Brain, BrainError
from .config import Config, Pipeline, normalise_path
from .target import LocalTarget, TargetError


def destination_type(destination: dict | None) -> str:
    return (destination or {}).get("type", "directory")


def is_github(pipeline: Pipeline) -> bool:
    return destination_type(pipeline.destination) == "github"


def directory_root(brain: Brain, destination: dict | None) -> Path:
    """The folder a directory destination points at (the brain's own folder when it names none)."""
    path = (destination or {}).get("path")
    return Path(normalise_path(path)) if path else brain.root


def destination_key(brain: Brain, pipeline: Pipeline) -> str:
    """Identity of the target: pipelines with the same key share one target (one state file, one repository branch)."""
    dest = pipeline.destination
    if destination_type(dest) == "github":
        return json.dumps({"github": (dest or {}).get("repo", "").lower(), "branch": (dest or {}).get("branch", ""),
                           "api_url": (dest or {}).get("api_url", "")}, sort_keys=True)
    return "directory:" + str(directory_root(brain, dest).resolve())


def credential_of(pipeline: Pipeline) -> str:
    """The credential reference a GitHub destination names ("" for none or a directory). A reference, never a secret."""
    dest = pipeline.destination or {}
    return dest.get("credential", "") if destination_type(dest) == "github" else ""


def conflicting_pipelines(brain: Brain, pipeline: Pipeline, others: list[Pipeline]) -> list[Pipeline]:
    """The other pipelines on the same GitHub repo and branch that name a different credential (MD-97).

    Such pipelines share one bound target, so one of them would write with an identity it did not choose."""
    mine = credential_of(pipeline)
    if not mine:
        return []
    key = destination_key(brain, pipeline)
    return [o for o in others if o.name != pipeline.name and destination_key(brain, o) == key and credential_of(o) not in ("", mine)]


def credential_conflict_of(brain: Brain, config: Config, pipeline: Pipeline) -> list[Pipeline]:
    """The saved pipelines whose credential clashes with ``pipeline``'s on the same repository and branch (empty: no conflict)."""
    return conflicting_pipelines(brain, pipeline, config.pipelines)


def credential_conflict_message(pipeline: Pipeline, conflicts: list[Pipeline]) -> str:
    """Plain words for the user; names the other pipelines and both ways out. Never a credential value."""
    names = ", ".join(f"'{c.name}'" for c in conflicts)
    dest = pipeline.destination or {}
    where = f"{dest.get('repo', '')} ({dest.get('branch') or 'default branch'})"
    return (f"Pipeline {names} already writes to {where} with a different credential, so pipeline '{pipeline.name}' cannot use "
            f"this repository and branch with the credential it chose. Use the same credential as {names}, or choose another branch.")


def bind_target(brain: Brain, pipeline: Pipeline) -> Brain:
    """The brain view whose ``target`` is this pipeline's target (cached per brain object, so one sync shares one target)."""
    dest = pipeline.destination
    kind = destination_type(dest)
    if kind == "directory" and directory_root(brain, dest).resolve() == brain.root.resolve():
        return brain
    key = destination_key(brain, pipeline)
    cache_key = ("bound", key)
    if cache_key not in brain._cache:
        brain._cache[cache_key] = Brain(brain.root, {"target": _make(brain, dest, kind), "destination": dest})
    return brain._cache[cache_key]


def _make(brain: Brain, dest: dict | None, kind: str):
    if kind == "directory":
        return LocalTarget(Brain(directory_root(brain, dest)))
    if kind == "github":
        from .github_target import GitHubTarget
        from .targetconfig import GitHubSpec, api_for

        spec = GitHubSpec.from_dict(dest or {})
        if not spec.credential:
            from .github_signin import SignInAgain
            from .target import TargetAuthError

            raise (SignInAgain("This pipeline has no GitHub sign-in any more. Sign in again.") if spec.auth == "sign-in"
                   else TargetAuthError("This pipeline has no GitHub token. Enter one: `mdpipeline pipeline edit NAME --replace-token`, or in the UI under its target."))
        try:
            return GitHubTarget(api_for(spec), spec.repo, spec.branch, label=spec.label(), clone=spec.clone)
        except ValueError as exc:
            raise TargetError(f"The GitHub target of this pipeline is not usable: {exc}") from None
    raise TargetError(f"Unknown target type {kind!r}.")


def describe_destination(brain: Brain, pipeline: Pipeline) -> dict:
    """How the destination is shown to the user (CLI, UI, MCP): what and where, never a secret."""
    dest = pipeline.destination
    if destination_type(dest) == "github":
        out = {"type": "github", "repo": dest.get("repo", ""), "branch": dest.get("branch", ""),
               "label": f"github {dest.get('repo', '')} ({dest.get('branch', '')})", "auth": dest.get("auth", "token")}
        if dest.get("clone"):
            out["clone"] = dest["clone"]
        return out
    root = directory_root(brain, dest)
    return {"type": "directory", "path": str(root), "label": f"directory {root}"}


class AmbiguousTarget(BrainError):
    """Several targets are in use and the command did not say which one (message is user-facing)."""


def target_group(brain: Brain, config: Config, name: str | None = None) -> tuple[Brain, list[Pipeline]]:
    """The target a state-level command (restore, rebuild) works on, and the pipelines that use it.

    ``name``: the target of that pipeline. Without it: the only target in use (the brain's own folder when no pipeline exists);
    when pipelines use several targets the command must say which one."""
    if name is not None:
        chosen = config.get(name)
        if chosen is None:
            raise BrainError(f"No pipeline named '{name}'.")
    elif not config.pipelines:
        return brain, []
    else:
        keys = {destination_key(brain, p) for p in config.pipelines}
        if len(keys) > 1:
            raise AmbiguousTarget("The pipelines use more than one target. Say which one with --pipeline NAME (the target of that pipeline).")
        chosen = config.pipelines[0]
    key = destination_key(brain, chosen)
    return bind_target(brain, chosen), [p for p in config.pipelines if destination_key(brain, p) == key]


def state_home(bound: Brain) -> Brain:
    """Where a local target keeps its state files and backups: the target's own folder (a view's control folder otherwise)."""
    return bound.target.brain if isinstance(bound.target, LocalTarget) else bound


DESTINATION_KEYS = {"directory": ("type", "path"), "github": ("type", "repo", "branch", "auth", "credential", "clone", "pending")}


def clean_destination(raw) -> dict | None:
    """A destination typed in a form or sent by the UI, reduced to its known text fields (an unknown key never reaches the config)."""
    if not raw or not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "directory").strip()
    keys = DESTINATION_KEYS.get(kind, ("type",))
    out = {k: str(raw[k]).strip() for k in keys if raw.get(k) not in (None, "")}
    out["type"] = kind
    if kind == "directory":
        if not out.get("path"):
            return None
        out["path"] = normalise_path(out["path"])
    return out


def ephemeral_github_view(brain: Brain, dest: dict, token: str) -> Brain:
    """A Brain view whose target is the typed-and-checked GitHub repository, using ``token`` from memory (never stored): for previews
    before a pipeline or a new target is saved. An empty branch means the repository's default branch."""
    from urllib.parse import urlsplit

    from .github_api import GitHubApi
    from .github_target import GitHubTarget
    from .targetconfig import GitHubSpec

    spec = GitHubSpec.from_dict(dest)
    host = urlsplit(spec.api_url).hostname or ""
    api = GitHubApi(token, api_url=spec.api_url, allowed_hosts=(host,))
    branch = spec.branch or api.repo(spec.repo).get("default_branch", "")
    return Brain(brain.root, {"target": GitHubTarget(api, spec.repo, branch, label=f"github {spec.repo} ({branch})", clone=spec.clone)})


def local_root(brain: Brain, pipeline: Pipeline) -> Path | None:
    """The folder on this machine that holds the pipeline's files: the brain's folder, a folder target, or the clone of a GitHub
    target. None for a GitHub target without a clone."""
    dest = pipeline.destination
    if destination_type(dest) == "github":
        clone = (dest or {}).get("clone")
        return Path(clone).expanduser() if clone else None
    return directory_root(brain, dest)
