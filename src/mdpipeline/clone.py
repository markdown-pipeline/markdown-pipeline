"""Keep an optional local clone of the GitHub brain current for agents (MD-67).

After a sync the clone is fast-forwarded with ``git fetch`` + ``git merge --ff-only``, and only when that cannot lose anything:
the clone must be a git work tree on the target branch, with no local changes (tracked or untracked), whose ``origin`` is
the https URL of the configured repository, and whose history has not diverged. Otherwise it is left exactly as it is and the
reason is reported ("Needs you"). Local commits and uncommitted work are never touched. The pipeline's own network rule is
kept: the only remote ever contacted is that https origin (git uses the user's own git credentials, never our token).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

GIT_TIMEOUT = 120


@dataclass(frozen=True)
class CloneResult:
    status: str          # updated | up-to-date | skipped
    reason: str = ""
    head: str = ""

    def to_dict(self) -> dict:
        d = {"status": self.status}
        if self.reason:
            d["reason"] = self.reason
        if self.head:
            d["head"] = self.head
        return d


def _git(path: str, *args: str) -> tuple[int, str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "LC_ALL": "C"}
    try:
        done = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", type(exc).__name__
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def _skipped(reason: str) -> CloneResult:
    return CloneResult("skipped", reason)


def _origin_ok(url: str, repo: str, host: str) -> bool:
    return url.rstrip("/") in (f"https://{host}/{repo}", f"https://{host}/{repo}.git")


def fast_forward_clone(path: str, repo: str, branch: str, host: str = "github.com") -> CloneResult:
    """Fast-forward the clone at ``path`` to the remote ``branch`` when that is safe; otherwise leave it and say why."""
    if not os.path.isdir(path):
        return _skipped(f"the clone folder {path} does not exist")
    code, out, _ = _git(path, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out != "true":
        return _skipped(f"{path} is not a git clone")
    code, url, _ = _git(path, "config", "--get", "remote.origin.url")
    if code != 0 or not _origin_ok(url, repo, host):
        return _skipped(f"the clone's origin must be https://{host}/{repo} (only that remote is contacted); it is {url or 'not set'}")
    code, current, _ = _git(path, "symbolic-ref", "--short", "-q", "HEAD")
    if code != 0 or current != branch:
        return _skipped(f"the clone is on branch '{current or 'a detached HEAD'}', not '{branch}'; it was left alone")
    code, status, _ = _git(path, "status", "--porcelain")
    if code != 0 or status:
        return _skipped("the clone has local changes; commit or stash them, then it will be updated on the next sync")
    code, _, stderr = _git(path, "fetch", "--quiet", "origin", branch)
    if code != 0:
        return _skipped("git fetch failed (" + (stderr.splitlines() or ["no details"])[0][:160] + "); check your git access to the repository")
    _, remote_head, _ = _git(path, "rev-parse", "FETCH_HEAD")
    _, local_head, _ = _git(path, "rev-parse", "HEAD")
    if remote_head == local_head:
        return CloneResult("up-to-date", head=local_head)
    if _git(path, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")[0] == 0:
        code, _, stderr = _git(path, "merge", "--ff-only", "--quiet", "FETCH_HEAD")
        if code != 0:
            return _skipped("the fast-forward was refused by git (" + (stderr.splitlines() or ["no details"])[0][:160] + ")")
        return CloneResult("updated", head=remote_head)
    if _git(path, "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD")[0] == 0:
        return CloneResult("up-to-date", "the clone has local commits that are not on the remote; they were left alone", local_head)
    return _skipped("the clone has diverged from the remote (local commits and new remote commits); it was left alone")
