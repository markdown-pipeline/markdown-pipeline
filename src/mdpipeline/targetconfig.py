"""Choosing and checking where a brain lives: the GitHub target's configuration, validation and connection test (MD-61).

The configuration holds the repository, the branch and a credential *reference* (``env:NAME`` or ``keychain:SERVICE``),
never a token. Validation reports plain messages and the caller saves nothing on failure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .credentials import CredentialError, parse_ref, resolve_token
from .github_api import DEFAULT_API_URL, GitHubApi
from .target import TargetError
from .validation import Issue, ValidationResult

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
SUGGESTED_BRANCH = "md-sync"


@dataclass
class GitHubSpec:
    repo: str
    credential: str
    branch: str = ""
    api_url: str = DEFAULT_API_URL
    clone: str = ""                       # optional local clone for agents (MD-67)
    auth: str = "token"                   # "token" (a personal access token) or "sign-in" (the GitHub App, MD-83)

    def to_dict(self) -> dict:
        d = {"type": "github", "repo": self.repo, "branch": self.branch, "credential": self.credential}
        if self.auth != "token":
            d["auth"] = self.auth
        if self.api_url != DEFAULT_API_URL:
            d["api_url"] = self.api_url
        if self.clone:
            d["clone"] = self.clone
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "GitHubSpec":
        return cls(repo=data.get("repo", ""), credential=data.get("credential", ""), branch=data.get("branch", ""),
                   api_url=data.get("api_url", DEFAULT_API_URL), clone=data.get("clone", ""), auth=data.get("auth", "token"))

    def label(self) -> str:
        return f"github {self.repo} ({self.branch})"


def api_for(spec: GitHubSpec) -> GitHubApi:
    """A client whose token is resolved on first use (so a missing variable is reported where it is needed)."""
    from urllib.parse import urlsplit

    host = urlsplit(spec.api_url).hostname or ""
    if spec.auth == "sign-in":
        from .github_signin import SignInCredential

        kind, name = parse_ref(spec.credential)
        return GitHubApi(SignInCredential(name).token if kind == "vault" else (lambda: resolve_token(spec.credential)),
                         api_url=spec.api_url, allowed_hosts=(host,))
    return GitHubApi(lambda: resolve_token(spec.credential), api_url=spec.api_url, allowed_hosts=(host,))


@dataclass
class Connection:
    user: str
    repo: str
    branch: str
    head_sha: str
    head_url: str
    can_write: bool
    protected: bool
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"user": self.user, "author": self.user, "repo": self.repo, "branch": self.branch, "head": self.head_sha, "head_url": self.head_url,
                "write_allowed": self.can_write, "protected": self.protected, "problems": self.problems}


def protected_message(branch: str) -> str:
    return (f"The branch '{branch}' is protected against direct pushes, so the pipeline cannot commit to it. "
            f"Use a dedicated branch such as '{SUGGESTED_BRANCH}' (create it in the repository first), or remove the protection.")


def test_connection(spec: GitHubSpec, api: GitHubApi | None = None) -> Connection:
    """Read-only check: the token's user, the repository, the branch head and whether a write would be allowed."""
    api = api or api_for(spec)
    user = api.user()
    installation = None
    if spec.auth == "sign-in":                # the GitHub App must be installed on this repository (a sign-in token sees nothing else)
        from .github_signin import install_url, installation_for

        installation = installation_for(api, spec.repo)
        if installation is None:
            raise TargetError(f"The GitHub App is not installed on {spec.repo} (or the name is wrong). Install it on this repository: "
                              f"{install_url()} and then test again.")
    repo = api.repo(spec.repo)
    branch = spec.branch or repo.get("default_branch", "")
    info = api.branch(spec.repo, branch)
    problems = []
    can_write = bool((repo.get("permissions") or {}).get("push"))
    if installation is not None and (installation.get("permissions") or {}).get("contents") != "write":
        can_write = False
        problems.append("The GitHub App is installed on this repository with read-only Contents. Allow it to write Contents (read and write) in the installation settings.")
    elif not can_write:
        problems.append("The token can only read this repository. It needs Contents read and write.")
    if info.get("protected"):
        can_write = False
        problems.append(protected_message(branch))
    sha = info["commit"]["sha"]
    return Connection(user, spec.repo, branch, sha, f"https://github.com/{spec.repo}/commit/{sha}", can_write, bool(info.get("protected")), problems)


def validate_github_spec(spec: GitHubSpec, api: GitHubApi | None = None) -> tuple[ValidationResult, Connection | None]:
    """Everything that must hold before a GitHub brain is saved. The result has errors when it must not be saved."""
    result = ValidationResult()
    err = lambda code, msg, fld="": result.errors.append(Issue(code, msg, fld))  # noqa: E731
    if not REPO_RE.match(spec.repo or ""):
        err("bad-repo", f"'{spec.repo}' is not a repository name; use OWNER/REPO.", "repo")
    if spec.branch and not BRANCH_RE.match(spec.branch):
        err("bad-branch", f"'{spec.branch}' is not a usable branch name.", "branch")
    try:
        parse_ref(spec.credential)
    except CredentialError as exc:
        err("bad-credential", str(exc), "credential")
    if result.errors:
        return result, None
    try:
        conn = test_connection(spec, api)
    except TargetError as exc:
        err("github-" + exc.category, str(exc))
        return result, None
    for problem in conn.problems:
        err("github-write", problem, "branch" if "protected" in problem else "credential")
    return result, conn
