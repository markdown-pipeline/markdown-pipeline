"""Build the package from this (private) source and publish it as a GitHub Release on the PUBLIC releases repo (MD-131).

    uv run python scripts/release.py                       # build, check the package, print what would be published (changes nothing)
    uv run python scripts/release.py --publish             # ... and publish it (needs `gh auth login` with write access to the repo)
    uv run python scripts/release.py --notes-file docs/release-notes-0.1.9.md --publish   # ... with prepared release notes
    uv run python scripts/release.py --publish --replace   # re-upload the wheel (and server.json) of a release that already exists (same version)
    uv run python scripts/release.py --publish --skip-repo-settings   # CI (DEV-11): its token has no Administration right, so no `gh repo edit`

server.json (version and packages[0].version must equal the release version, checked first) is attached next to the wheel in the one create call,
because the public repo's publish-registry workflow publishes the release's server.json asset to the MCP Registry when the release is published.

For the next fix release: raise ``version`` in pyproject.toml, run ``uv lock`` and commit uv.lock with it (MD-169: otherwise every ``uv run``
rewrites the lock's version string; this script refuses to build while they differ), run the tests, run this with --publish. The public repo name is a setting
(``--repo`` or MDPIPELINE_RELEASE_REPO) because it may still change. Only the wheel is attached: no sdist, so tests and docs stay private.
Before anything is published the wheel is checked: only the package, no tests/docs/scripts/lock files, no keys or tokens inside, and the public
GitHub App Client ID present (MD-129).
Glama rebuilds by itself on the public release (DEV-12: its Admin build installs the newest mdpipeline, Auto-Release on); glama.json mirrors that form and needs no bump. The PostHog project key is public and ingest-only; it ships on purpose.
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
SERVER_JSON = APP / "server.json"          # DEV-11: attached to the release; the public repo's publish-registry workflow publishes it to the MCP Registry

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


def server_json_problem(text: str, version: str) -> str | None:
    """Why this server.json must not be released (``None`` = fine): its ``version`` and ``packages[0].version`` must both be the release version."""
    try:
        data = json.loads(text)
        top, package = data.get("version"), data["packages"][0].get("version")
    except (ValueError, AttributeError, KeyError, IndexError, TypeError):
        return "server.json is not readable: it needs a version and packages[0].version"
    if top != version:
        return f"server.json version is {top}, the release is {version}: raise it with the package version"
    if package != version:
        return f"server.json packages[0].version is {package}, the release is {version}: raise it with the package version"
    return None


def check_server_json(version: str, path: Path | None = None) -> None:
    path = path or SERVER_JSON
    try:
        problem = server_json_problem(path.read_text(encoding="utf-8"), version)
    except OSError as exc:
        problem = f"cannot read {path}: {exc.strerror or exc}"
    if problem:
        raise ReleaseError(problem)


def release_plan(version: str, repo: str, wheel: Path, *, exists: bool, replace: bool = False, notes: str | None = None,
                 server_json: Path | None = None) -> list[list[str]]:
    tag = tag_for(version)
    assets = [str(wheel), *([str(server_json)] if server_json else [])]          # one call: the asset is there when the release-published event fires
    if exists and not replace:
        raise ReleaseError(f"release {tag} already exists on {repo}: raise the version in pyproject.toml, or pass --replace to re-upload the wheel")
    if exists:
        return [["gh", "release", "upload", tag, *assets, "--repo", repo, "--clobber"]]
    return [["gh", "release", "create", tag, *assets, "--repo", repo, "--title", f"Markdown Pipeline {version}",
             "--notes", notes if notes is not None else f"Markdown Pipeline {version}. Install: see the README of this repository.", "--latest"]]


REPO_DESCRIPTION = ("Your team's tools as clean Markdown files, kept current for your AI agents, with a local MCP server. "
                    "Early access: Jira Cloud and local folders, Linux. Apache-2.0.")
REPO_TOPICS = ["markdown", "mcp", "mcp-server", "model-context-protocol", "jira", "claude-code", "codex",
               "ai-agents", "llm", "knowledge-base", "llm-wiki", "etl"]


def repo_settings_command(repo: str) -> list[str]:
    """MM-121: the public repo's own settings. The README sends people to Issues for feedback, and the description is marketing's (idempotent)."""
    topics = [arg for topic in REPO_TOPICS for arg in ("--add-topic", topic)]
    return ["gh", "repo", "edit", repo, "--enable-issues", "--description", REPO_DESCRIPTION, *topics]


_SYNC_PUBLIC = None


def sync_public_module():
    """scripts/sync_public.py, loaded once by path (``scripts`` is not a package). Its ``WITHHELD`` set is the one switch for what may be public yet."""
    global _SYNC_PUBLIC
    if _SYNC_PUBLIC is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("sync_public", APP / "scripts" / "sync_public.py")
        _SYNC_PUBLIC = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SYNC_PUBLIC)
    return _SYNC_PUBLIC


def sync_public_files(repo: str, runner: Callable[[list[str]], tuple[int, str]], *, workdir: Path, publish: bool, version: str = "") -> bool:
    """Make the public repo's main branch equal this checkout's tracked tree (``sync_public.apply``: the whole source, the root copies, never ``.github/workflows/``),
    in one commit ``Source and public files at vX.Y.Z`` pushed to main; nothing changed means no commit. A dry run clones (read only) and prints the plan.
    Returns whether it pushed."""
    sync = sync_public_module()
    clone = workdir / "repo"
    code, text = runner(["gh", "repo", "clone", repo, str(clone)])
    if code != 0 or not (clone / ".git").exists():
        if publish:
            raise ReleaseError("could not clone the public repository:\n" + text)
        print(f"DRY RUN, could not clone {repo} to plan the sync ({text.splitlines()[-1] if text else 'no answer'}): it would sync {len(sync.wanted(APP))} tracked files")
        return False
    write, delete = sync.plan(clone, APP)
    verb = "" if publish else "DRY RUN, "
    print(f"{verb}{'syncing' if publish else 'would sync'} {repo}: {'' if publish else 'would '}write {len(write)} file(s), {'' if publish else 'would '}delete {len(delete)}")
    for p in write:
        print("  write ", p)
    for p in delete:
        print("  delete", p)
    if not publish:
        return False
    sync.apply(clone, APP)
    if (clone / "install.sh").exists():
        (clone / "install.sh").chmod(0o755)
    git = ["git", "-C", str(clone)]
    runner([*git, "add", "-A"])
    code, changed = runner([*git, "status", "--porcelain"])
    if code != 0 or not changed.strip():
        print("public files: already up to date")
        return False
    message = f"Source and public files at v{version}" if version else "Source and public files"
    for cmd in ([*git, "commit", "-m", message], ["git", "-C", str(clone), "-c", "credential.https://github.com.helper=!gh auth git-credential", "push", "origin", "HEAD:main"]):
        code, text = runner(cmd)
        if code != 0:
            raise ReleaseError("syncing the public files failed:\n" + text)
    print(f"public files: synced to {repo}")
    return True


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, cwd=APP)
    except OSError as error:  # the program is not installed
        return 127, f"{cmd[0]}: {error.strerror or error}"
    return done.returncode, (done.stdout + done.stderr).strip()


PRIVATE_REPO = os.environ.get("MDPIPELINE_PRIVATE_REPO", "shvil-ai/md-pipeline-app")          # where linux-tests / windows-tests run (MD-214)
GATED_WORKFLOWS = ("linux-tests", "windows-tests")


def ci_problem(workflow: str, runs: list[dict], shas: set[str], repo: str = PRIVATE_REPO) -> str | None:
    """MD-214: why the newest ``workflow`` run on one of ``shas`` does not allow a release, or None when it is green. Names the run's URL."""
    mine = sorted((r for r in runs if r.get("headSha") in shas), key=lambda r: r.get("createdAt", ""))
    if not mine:
        latest = sorted(runs, key=lambda r: r.get("createdAt", ""))[-1:] or [None]
        seen = f" The latest run is on another commit ({latest[0]['headSha'][:7]}): {latest[0]['url']}" if latest[0] else ""
        return (f"no {workflow} run on the release commit.{seen} Start one by hand and wait for it: "
                f"gh workflow run {workflow} --repo {repo} --ref <the release branch or tag>")
    last = mine[-1]
    if last.get("status") != "completed":
        return f"{workflow} is still running on the release commit: {last['url']}"
    if last.get("conclusion") != "success":
        return f"{workflow} is not green on the release commit ({last.get('conclusion') or 'no result'}): {last['url']}"
    return None


def _ci_gate(args, runner: Callable[[list[str]], tuple[int, str]], version: str, notes: str | None) -> int:
    """``--check-ci`` / ``--create-draft`` (MD-214): both CI workflows green on exactly the tagged release commit, else stop; Windows has a logged override."""
    tag = tag_for(version)
    if args.allow_red_windows is not None and not args.allow_red_windows.strip():
        raise ReleaseError("--allow-red-windows needs a reason (it is printed and recorded in the draft's notes)")
    code, text = runner(["git", "rev-parse", f"{tag}^{{commit}}"])
    if code != 0:
        raise ReleaseError(f"tag {tag} does not exist here: tag the release commit on main first (git tag {tag} <sha> && git push origin {tag})")
    sha = text.strip().splitlines()[-1]
    code, text = runner(["git", "rev-list", "--parents", "-n", "1", sha])
    parents = text.split()[1:] if code == 0 else []
    # linux-tests runs on the pull request, so its run carries the PR head, the second parent of the merge commit the tag points at
    accepted = {"linux-tests": {sha, *parents[1:2]}, "windows-tests": {sha}}
    problems: dict[str, str] = {}
    for workflow in GATED_WORKFLOWS:
        code, text = runner(["gh", "run", "list", "--repo", args.private_repo, "--workflow", workflow, "--limit", "50",
                             "--json", "databaseId,headSha,status,conclusion,url,createdAt,event"])
        if code != 0:
            raise ReleaseError(f"could not read the {workflow} runs from {args.private_repo}:\n{text}")
        try:
            runs = json.loads(text or "[]")
        except ValueError as exc:
            raise ReleaseError(f"could not read the {workflow} runs from {args.private_repo}: {exc}") from exc
        problem = ci_problem(workflow, runs, accepted[workflow], args.private_repo)
        if problem:
            problems[workflow] = problem
    override = ""
    if "windows-tests" in problems and args.allow_red_windows is not None:
        override = f"windows-tests override: {args.allow_red_windows.strip()} ({problems.pop('windows-tests')})"
        print("OVERRIDE " + override)
    if problems:
        raise ReleaseError("not releasing; " + "; ".join(problems.values()))
    print(f"CI gate: linux-tests and windows-tests are green on {tag} ({sha[:7]})" + (" (windows-tests overridden)" if override else ""))
    if not args.create_draft:
        return 0
    body = (notes if notes is not None else f"Markdown Pipeline {version}") + (f"\n\n---\nRelease gate {override}\n" if override else "")
    code, text = runner(["gh", "release", "create", tag, "--repo", args.private_repo, "--draft", "--verify-tag", "--title", f"Markdown Pipeline {version}",
                         "--notes", body])
    if code != 0:
        raise ReleaseError("creating the draft release failed:\n" + text)
    print(f"draft release created for {tag} on {args.private_repo} (commit {sha}): {text.strip()}")
    return 0


def run(argv: list[str] | None = None, runner: Callable[[list[str]], tuple[int, str]] = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("MDPIPELINE_RELEASE_REPO", DEFAULT_REPO))
    parser.add_argument("--wheel", help="use this wheel instead of building one")
    parser.add_argument("--version", help="default: the version in pyproject.toml")
    parser.add_argument("--notes-file", help="the release notes (Markdown) to publish with the release; default: a one-line note")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--skip-repo-settings", action="store_true", help="with --publish: do not run `gh repo edit` (it needs Administration rights; CI's token has none)")
    parser.add_argument("--check-ci", action="store_true", help="MD-214: only check that linux-tests and windows-tests are green on the tagged release commit")
    parser.add_argument("--create-draft", action="store_true", help="MD-214: check as --check-ci, then create the draft release on the private repo for the tag")
    parser.add_argument("--allow-red-windows", metavar="REASON", help="with --create-draft: go ahead although windows-tests is not green; the reason is printed and recorded in the draft")
    parser.add_argument("--private-repo", default=PRIVATE_REPO, help="the private repo whose CI runs are checked; default: " + PRIVATE_REPO)
    parser.add_argument("--server-json", default=str(SERVER_JSON), help="attached to the release; default: server.json in the app folder")
    args = parser.parse_args(argv)
    try:
        stale = lock_problem((APP / "pyproject.toml").read_text(encoding="utf-8"), (APP / "uv.lock").read_text(encoding="utf-8"))
        if stale:
            raise ReleaseError(stale)
        version = args.version or version_from_pyproject((APP / "pyproject.toml").read_text(encoding="utf-8"))
        check_server_json(version, Path(args.server_json))          # before anything is built or published
        notes = None
        if args.notes_file:
            try:
                notes = Path(args.notes_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise ReleaseError(f"cannot read the release notes file {args.notes_file}: {exc.strerror or exc}") from exc
        if args.check_ci or args.create_draft:
            return _ci_gate(args, runner, version, notes)
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
        if args.skip_repo_settings:
            print(("" if args.publish else "DRY RUN: ") + "repo settings skipped (--skip-repo-settings): no `gh repo edit`")
        elif args.publish:
            code, text = runner(repo_settings_command(args.repo))
            if code != 0:
                raise ReleaseError("could not set the repository's description and Issues:\n" + text)
        sync_public_files(args.repo, runner, workdir=Path(tempfile.mkdtemp(prefix="mdp-public-")), publish=args.publish, version=version)
        exists = False
        if args.publish:
            exists = runner(["gh", "release", "view", tag_for(version), "--repo", args.repo])[0] == 0
        plan = release_plan(version, args.repo, wheel, exists=exists, replace=args.replace, notes=notes, server_json=Path(args.server_json))
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
