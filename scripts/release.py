"""Build the package from this (private) source and publish it as a GitHub Release on the PUBLIC releases repo (MD-131).

    uv run python scripts/release.py                       # build, check the package, print what would be published (changes nothing)
    uv run python scripts/release.py --publish             # ... and publish it (needs `gh auth login` with write access to the repo)
    uv run python scripts/release.py --notes-file docs/release-notes-0.1.9.md --publish   # ... with prepared release notes
    uv run python scripts/release.py --publish --replace   # re-upload the wheel of a release that already exists (same version)

For the next fix release: raise ``version`` in pyproject.toml, run ``uv lock`` and commit uv.lock with it (MD-169: otherwise every ``uv run``
rewrites the lock's version string; this script refuses to build while they differ), run the tests, run this with --publish. The public repo name is a setting
(``--repo`` or MDPIPELINE_RELEASE_REPO) because it may still change. Only the wheel is attached: no sdist, so tests and docs stay private.
Before anything is published the wheel is checked: only the package, no tests/docs/scripts/lock files, no keys or tokens inside, and the public
GitHub App Client ID present (MD-129). The PostHog project key is public and ingest-only; it ships on purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

APP = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "markdown-pipeline/markdown-pipeline"

_FORBIDDEN_NAME = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.pem|[^/]*\.key|id_rsa[^/]*|__pycache__|[^/]*\.pyc|\.git|\.git[^/]*)(/|$)")
_SECRETS = {
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "Atlassian API token": re.compile(r"\bATATT[A-Za-z0-9_\-=]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
}


class ReleaseError(Exception):
    pass


def version_from_pyproject(text: str) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise ReleaseError("no version found in pyproject.toml")
    return match.group(1)


def lock_problem(pyproject_text: str, lock_text: str) -> str | None:
    """Why uv.lock is not in step with pyproject.toml (``None`` = in step). The fix is always the same: ``uv lock`` and commit it."""
    import tomllib

    project = tomllib.loads(pyproject_text)["project"]
    mine = [p for p in tomllib.loads(lock_text).get("package", []) if p.get("name") == project["name"]]
    locked = mine[0].get("version") if mine else None
    if locked == project["version"]:
        return None
    return (f"uv.lock says {locked or 'nothing'} for {project['name']} but pyproject.toml says {project['version']}: "
            "run `uv lock` and commit uv.lock together with the version bump")


def tag_for(version: str) -> str:
    return f"v{version}"


def wheel_problems(wheel: Path) -> list[str]:
    """Why this wheel must not be published. Secrets are named by rule and file, never printed."""
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        for name in names:
            if not (name.startswith("mdpipeline/") or re.match(r"mdpipeline-[^/]+\.dist-info/", name)):
                problems.append(f"{name}: not part of the package (only mdpipeline/ and its dist-info may be published)")
            elif _FORBIDDEN_NAME.search(name):
                problems.append(f"{name}: dev-only or secret-looking file name")
            try:
                text = z.read(name).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for rule, rx in _SECRETS.items():
                if rx.search(text):
                    problems.append(f"{name}: contains a {rule}")
        for name, needle in (("mdpipeline/usage_events.py", "install_completed"), ("mdpipeline/usage.py", "is_internal")):
            if name not in names or needle not in z.read(name).decode("utf-8", "replace"):
                problems.append(f"{name} does not contain {needle!r}: the launch-day usage data (MD-154) would be missing from this build")
        if "mdpipeline/defaults.json" not in names:
            problems.append("mdpipeline/defaults.json is missing: the public GitHub App Client ID would not ship")
        else:
            app = (json.loads(z.read("mdpipeline/defaults.json")).get("github_app") or {})
            if not app.get("client_id") or not app.get("slug"):
                problems.append("mdpipeline/defaults.json has no GitHub App Client ID and slug (MD-129): sign-in would ask people to register an app")
    return problems


def release_plan(version: str, repo: str, wheel: Path, *, exists: bool, replace: bool = False, notes: str | None = None) -> list[list[str]]:
    tag = tag_for(version)
    if exists and not replace:
        raise ReleaseError(f"release {tag} already exists on {repo}: raise the version in pyproject.toml, or pass --replace to re-upload the wheel")
    if exists:
        return [["gh", "release", "upload", tag, str(wheel), "--repo", repo, "--clobber"]]
    return [["gh", "release", "create", tag, str(wheel), "--repo", repo, "--title", f"Markdown Pipeline {version}",
             "--notes", notes if notes is not None else f"Markdown Pipeline {version}. Install: see the README of this repository.", "--latest"]]


REPO_DESCRIPTION = ("Your team's tools as clean Markdown files, kept current for your AI agents, with a local MCP server. "
                    "Early access: Jira Cloud and local folders, Linux. Apache-2.0.")
REPO_TOPICS = ["markdown", "mcp", "mcp-server", "model-context-protocol", "jira", "claude-code", "codex",
               "ai-agents", "llm", "knowledge-base", "llm-wiki", "etl"]


def repo_settings_command(repo: str) -> list[str]:
    """MM-121: the public repo's own settings. The README sends people to Issues for feedback, and the description is marketing's (idempotent)."""
    topics = [arg for topic in REPO_TOPICS for arg in ("--add-topic", topic)]
    return ["gh", "repo", "edit", repo, "--enable-issues", "--description", REPO_DESCRIPTION, *topics]


def public_files() -> dict[str, Path]:
    """What the public repo holds besides the Releases: its README, the install script and the plain-words pages. No source code."""
    return {"README.md": APP / "README.md", "install.sh": APP / "scripts" / "install.sh",
            "what-we-send.md": APP / "docs" / "what-we-send.md", "usage-events.md": APP / "docs" / "usage-events.md"}


def sync_public_files(repo: str, runner: Callable[[list[str]], tuple[int, str]], *, workdir: Path, publish: bool) -> bool:
    """Put :func:`public_files` on the public repo's main branch (clone, copy, commit when something changed, push). Returns whether it pushed."""
    if not publish:
        print("DRY RUN, would sync to " + repo + ": " + ", ".join(public_files()))
        return False
    clone = workdir / "repo"
    code, text = runner(["gh", "repo", "clone", repo, str(clone)])
    if code != 0:
        raise ReleaseError("could not clone the public repository:\n" + text)
    clone.mkdir(parents=True, exist_ok=True)
    for name, src in public_files().items():
        (clone / name).write_bytes(src.read_bytes())
    (clone / "install.sh").chmod(0o755)
    git = ["git", "-C", str(clone)]
    runner([*git, "add", "-A"])
    code, changed = runner([*git, "status", "--porcelain"])
    if code != 0 or not changed.strip():
        print("public files: already up to date")
        return False
    for cmd in ([*git, "commit", "-m", "Update README, install script and plain-words pages"], ["git", "-C", str(clone), "-c", "credential.https://github.com.helper=!gh auth git-credential", "push", "origin", "HEAD:main"]):
        code, text = runner(cmd)
        if code != 0:
            raise ReleaseError("syncing the public files failed:\n" + text)
    print(f"public files: synced to {repo}")
    return True


def _run(cmd: list[str]) -> tuple[int, str]:
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=APP)
    return done.returncode, (done.stdout + done.stderr).strip()


def run(argv: list[str] | None = None, runner: Callable[[list[str]], tuple[int, str]] = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("MDPIPELINE_RELEASE_REPO", DEFAULT_REPO))
    parser.add_argument("--wheel", help="use this wheel instead of building one")
    parser.add_argument("--version", help="default: the version in pyproject.toml")
    parser.add_argument("--notes-file", help="the release notes (Markdown) to publish with the release; default: a one-line note")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        stale = lock_problem((APP / "pyproject.toml").read_text(encoding="utf-8"), (APP / "uv.lock").read_text(encoding="utf-8"))
        if stale:
            raise ReleaseError(stale)
        version = args.version or version_from_pyproject((APP / "pyproject.toml").read_text(encoding="utf-8"))
        notes = None
        if args.notes_file:
            try:
                notes = Path(args.notes_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise ReleaseError(f"cannot read the release notes file {args.notes_file}: {exc.strerror or exc}") from exc
        if args.wheel:
            wheel = Path(args.wheel)
        else:
            out = Path(tempfile.mkdtemp(prefix="mdp-release-"))
            code, text = runner(["uv", "build", "--wheel", "--out-dir", str(out)])
            if code != 0:
                raise ReleaseError("the build failed:\n" + text)
            built = sorted(out.glob("mdpipeline-*.whl"))
            if not built:
                raise ReleaseError("the build produced no wheel")
            wheel = built[-1]
        problems = wheel_problems(wheel)
        if problems:
            print("package checks: FAILED, nothing was published")
            for p in problems:
                print("  -", p)
            return 1
        print(f"package checks: OK ({wheel.name})")
        # Order matters: a repository with no commit cannot have a release (HTTP 422 "Repository is empty"), so the repo settings and the
        # public files (the first commit) go first, then the release.
        if args.publish:
            code, text = runner(repo_settings_command(args.repo))
            if code != 0:
                raise ReleaseError("could not set the repository's description and Issues:\n" + text)
        sync_public_files(args.repo, runner, workdir=Path(tempfile.mkdtemp(prefix="mdp-public-")), publish=args.publish)
        exists = False
        if args.publish:
            exists = runner(["gh", "release", "view", tag_for(version), "--repo", args.repo])[0] == 0
        plan = release_plan(version, args.repo, wheel, exists=exists, replace=args.replace, notes=notes)
        for cmd in plan:
            print(("" if args.publish else "DRY RUN, would run: ") + " ".join(cmd))
        if args.publish:
            for cmd in plan:
                code, text = runner(cmd)
                if code != 0:
                    raise ReleaseError("publishing failed:\n" + text)
            print(f"published {tag_for(version)} on {args.repo}")
        return 0
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
