"""Live verification of the GitHub target against a REAL scratch repository (MD-73).

Never run by the test suite against GitHub (the suite runs this very code against the fake). Run it by hand with a throw-away repository
and a token for that one repository (Contents read and write):

    uv run python scripts/live_github_check.py --repo OWNER/SCRATCH --scratch-repo-confirmed [--protected-branch NAME] [--report report.json]

The token is read from a hidden prompt (or one line of stdin with --stdin, or a vault secret with --use-secret ID); it is never an argument.
Everything is written to a NEW branch ``mdp-live-check-<time>`` created from the default branch; the default branch and every other branch are
never changed. ``--cleanup`` deletes the work branch at the end. Each check prints PASS / FAIL / MANUAL with what it saw; a FAIL or a response
that differs from what the fake assumes is a divergence to file as a bug (and to fix in tests/fake_github.py).
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdpipeline import github_api  # noqa: E402
from mdpipeline.brain import init_brain  # noqa: E402
from mdpipeline.clone import fast_forward_clone  # noqa: E402
from mdpipeline.config import Pipeline, load_config, save_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.github_api import GitHubApi  # noqa: E402
from mdpipeline.github_target import STATE_PATH  # noqa: E402
from mdpipeline.target import TargetError  # noqa: E402
from mdpipeline.targetconfig import GitHubSpec, test_connection, validate_github_spec  # noqa: E402

PASS, FAIL, MANUAL = "PASS", "FAIL", "MANUAL"
TOKEN_VAR = "MDP_LIVE_CHECK_TOKEN"            # set for this process only (never persisted): the engine's real credential path reads it
TOKEN_REF = f"env:{TOKEN_VAR}"


@dataclass
class Result:
    item: str
    title: str
    status: str
    detail: str = ""
    divergences: list[str] = field(default_factory=list)


class Recorder:
    """Wraps the transport to remember the headers of the last responses (rate-limit headers are part of the checklist)."""

    def __init__(self, inner=None) -> None:
        self.inner = inner
        self.headers: list[dict] = []
        self.requests = 0

    def __call__(self, method, url, headers, body):
        self.requests += 1
        transport = self.inner or (lambda m, u, h, b: github_api.urllib_transport(m, u, h, b, allowed_host="api.github.com"))
        status, resp_headers, raw = transport(method, url, headers, body)
        self.headers.append({k.lower(): v for k, v in resp_headers.items()})
        return status, resp_headers, raw


class Checker:
    def __init__(self, repo: str, token: str, protected_branch: str | None, transport=None, work_dir: Path | None = None, files_for_scale: int = 500) -> None:
        self.repo, self.token, self.protected = repo, token, protected_branch
        self.recorder = Recorder(transport)
        github_api.set_transport(self.recorder)
        os.environ[TOKEN_VAR] = token
        self.api = GitHubApi(token)
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="mdp-live-"))
        self.scale = files_for_scale
        self.results: list[Result] = []
        self.branch = f"mdp-live-check-{int(time.time())}"
        self.default_branch = ""

    # -- plumbing ---------------------------------------------------------------------
    def add(self, item: str, title: str, status: str, detail: str = "", divergences: list[str] | None = None) -> None:
        self.results.append(Result(item, title, status, detail, divergences or []))
        print(f"[{status}] {item} {title}" + (f": {detail}" if detail else ""), flush=True)
        for d in divergences or []:
            print(f"        DIVERGENCE: {d}", flush=True)

    def spec(self) -> GitHubSpec:
        return GitHubSpec(self.repo, TOKEN_REF, self.branch)

    def new_brain(self, name: str):
        root = self.work_dir / name
        (root / "raw").mkdir(parents=True, exist_ok=True)
        return init_brain(root)

    def make_pipeline(self, brain, name: str, files: dict[str, str], **rules) -> Path:
        src = self.work_dir / f"src-{name}"
        for rel, text in files.items():
            path = src / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        cfg = load_config(brain)
        dest = {"type": "github", "repo": self.repo, "branch": self.branch, "credential": TOKEN_REF}
        cfg.pipelines.append(Pipeline.new(name, [str(src)], destination=dest, **rules))
        save_config(brain, cfg)
        return src

    def commits(self) -> list[dict]:
        return self.api.history(self.repo, self.branch, "", 100)

    def other_commit(self, files: dict[str, bytes | None], message: str) -> str:
        """Someone else commits to the work branch through the API (None deletes a path)."""
        api = self.api
        head = api.head(self.repo, self.branch)
        tree = api.commit(self.repo, head)["tree"]["sha"]
        entries = []
        for path, data in files.items():
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None if data is None else api.create_blob(self.repo, data)})
        new_tree = api.create_tree(self.repo, tree, entries)
        sha = api.create_commit(self.repo, message, new_tree, [head])["sha"]
        api.update_ref(self.repo, self.branch, sha)
        return sha

    # -- the checklist ----------------------------------------------------------------
    def run(self) -> list[Result]:
        try:
            if not self.item_1_registration():
                return self.results
            self.create_work_branch()
            self.item_2_atomic_commit()
            self.item_3_branch_moves()
            self.item_4_edited_and_deleted()
            self.item_5_limits()
            self.item_6_clone()
        finally:
            os.environ.pop(TOKEN_VAR, None)
        return self.results

    def item_1_registration(self) -> bool:
        spec = GitHubSpec(self.repo, TOKEN_REF, "")
        divergences: list[str] = []
        try:
            user = self.api.user()
            repo = self.api.repo(self.repo)
            self.default_branch = repo.get("default_branch", "")
            info = self.api.branch(self.repo, self.default_branch)
        except TargetError as exc:
            self.add("1", "registration validation: test connection", FAIL, str(exc))
            return False
        for key, present in (("repo.permissions.push", "push" in (repo.get("permissions") or {})), ("repo.default_branch", bool(self.default_branch)),
                             ("branch.protected", "protected" in info), ("branch.commit.sha", bool((info.get("commit") or {}).get("sha")))):
            if not present:
                divergences.append(f"the real response has no {key} (the target relies on it)")
        can_write = bool((repo.get("permissions") or {}).get("push"))
        self.add("1a", "test connection: user, repo, default branch head, write allowed", PASS if can_write and not divergences else FAIL,
                 f"user={user} default_branch={self.default_branch} write={'allowed' if can_write else 'NOT allowed'}", divergences)
        if self.protected:
            result, conn = validate_github_spec(GitHubSpec(self.repo, TOKEN_REF, self.protected))
            ok = (not result.ok) and any("md-sync" in i.message for i in result.errors)
            self.add("1b", f"protected branch {self.protected} is refused with a dedicated-branch suggestion", PASS if ok else FAIL,
                     " | ".join(i.message for i in result.errors)[:200] or "it was accepted")
            self.protected_ref_probe()
        else:
            self.add("1b", "protected branch is refused", MANUAL, "re-run with --protected-branch NAME (a branch protected against direct pushes in the scratch repo)")
        self.add("1c", "a token without write is refused", MANUAL, "re-run once with a Contents READ-ONLY token for this repo: expect 'read and write' in the message")
        probe = self.unknown_repo_probe()
        if probe:
            self.add("1d", "error shapes: missing repository is 404, a bad token is 401", PASS if not probe else FAIL, "", probe)
        else:
            self.add("1d", "error shapes: missing repository is 404, a bad token is 401", PASS, "")
        return True

    def unknown_repo_probe(self) -> list[str]:
        out = []
        try:
            self.api.repo(self.repo + "-does-not-exist-xyz")
            out.append("a missing repository did not raise")
        except TargetError as exc:
            if "Not found" not in str(exc):
                out.append(f"a missing repository gave: {exc}")
        try:
            GitHubApi("ghp_notarealtoken0000").user()
            out.append("a bad token was accepted")
        except TargetError as exc:
            if exc.category != "auth":
                out.append(f"a bad token gave category {exc.category}")
        return out

    def protected_ref_probe(self) -> None:
        """What GitHub really answers when a protected branch is updated: the target relies on the word 'protect' in the message."""
        try:
            head = self.api.head(self.repo, self.protected)
            commit = self.api.commit(self.repo, head)
            tree = self.api.create_tree(self.repo, commit["tree"]["sha"], [])
            sha = self.api.create_commit(self.repo, "mdpipeline live check (never applied)", tree, [head])["sha"]
            self.api.update_ref(self.repo, self.protected, sha)
            self.add("1b2", f"updating the protected branch {self.protected} is refused", FAIL, "the update WAS accepted: the branch is not really protected against direct pushes; check the repo")
        except TargetError as exc:
            self.add("1b2", f"updating the protected branch {self.protected} is refused as protected-branch", PASS if exc.category == "protected-branch" else FAIL,
                     f"category={exc.category}", [] if exc.category == "protected-branch" else ["the real message did not contain 'protect': update github_api._error and the fake"])

    def create_work_branch(self) -> None:
        head = self.api.head(self.repo, self.default_branch)
        self.api.request("POST", f"/repos/{self.repo}/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": head}, what="the work branch")
        print(f"work branch: {self.branch} (created from {self.default_branch}); nothing else is changed", flush=True)

    def item_2_atomic_commit(self) -> None:
        brain = self.new_brain("b2")
        src = self.make_pipeline(brain, "p", {"a.md": "A", "b.md": "B", "c.md": "C", "sub/d.md": "D"}, name_policy="original", guard_threshold_percent=100)
        before = len(self.commits())
        res = sync_pipeline(brain, "p", trigger="cli")
        after = self.commits()
        ok = res.result == "ok" and len(after) == before + 1
        self.add("2a", "a first sync of 4 files is exactly one commit (content + state)", PASS if ok else FAIL, f"result={res.result} new commits={len(after) - before}")
        (src / "a.md").write_text("A2")
        (src / "b.md").unlink()
        (src / "e.md").write_text("E")
        os.rename(src / "c.md", src / "c2.md")
        before = len(self.commits())
        res = sync_pipeline(brain, "p", trigger="cli")
        counts = res.counts()
        ok = res.result == "ok" and len(self.commits()) == before + 1
        self.add("2b", "update + delete + add + rename of source files is one commit", PASS if ok else FAIL,
                 f"counts={ {k: v for k, v in counts.items() if v} } new commits={len(self.commits()) - before}")
        head = self.api.head(self.repo, self.branch)
        res = sync_pipeline(brain, "p", trigger="cli")
        self.add("2c", "a sync with no changes makes no commit", PASS if res.result == "ok" and res.commit is None and self.api.head(self.repo, self.branch) == head else FAIL,
                 f"commit={res.commit and res.commit.sha[:7]}")
        state = self.api.blob(self.repo, {e["path"]: e for e in self.api.tree(self.repo, self.api.commit(self.repo, self.api.head(self.repo, self.branch))["tree"]["sha"])}[STATE_PATH]["sha"])
        self.add("2d", "the state file in the repo holds paths and hashes only (no content, no secret)",
                 PASS if b"A2" not in state and self.token.encode() not in state else FAIL, f"{len(state)} bytes")
        self.brain2, self.src2 = brain, src

    def item_3_branch_moves(self) -> None:
        brain, src = self.brain2, self.src2
        (src / "moving.md").write_text("M")
        target = None
        from mdpipeline.destinations import bind_target

        target = bind_target(brain, load_config(brain).get("p")).target
        original = target.commit
        calls = {"n": 0}

        def commit_after_someone_else(message):
            calls["n"] += 1
            if calls["n"] == 1:
                self.other_commit({"wiki/teammate.md": b"a teammate committed while the sync ran"}, "teammate")
            return original(message)

        target.commit = commit_after_someone_else
        try:
            res = sync_pipeline(brain, "p", trigger="cli")
        finally:
            target.commit = original
        history = self.commits()
        merges = any(len(c.get("parents", [])) > 1 for c in history)
        tree = {e["path"] for e in self.api.tree(self.repo, self.api.commit(self.repo, self.api.head(self.repo, self.branch))["tree"]["sha"])}
        ok = res.result == "ok" and "wiki/teammate.md" in tree and any(p.endswith("moving.md") for p in tree) and not merges
        self.add("3", "a commit made by someone else mid-sync is kept; ours goes on top; no force; the sync retried", PASS if ok else FAIL,
                 f"result={res.result} attempts={calls['n']} teammate file kept={'wiki/teammate.md' in tree}")
        sha = self.api.head(self.repo, self.branch)
        old = self.api.commit(self.repo, sha)["parents"][0]["sha"]
        try:
            stale = self.api.create_commit(self.repo, "stale", self.api.commit(self.repo, old)["tree"]["sha"], [old])["sha"]
            self.api.update_ref(self.repo, self.branch, stale)
            self.add("3b", "a non-fast-forward ref update is refused by GitHub", FAIL, "it was accepted: the branch could be rewound")
            self.api.update_ref(self.repo, self.branch, sha)
        except TargetError as exc:
            self.add("3b", "a non-fast-forward ref update is refused by GitHub as head-moved", PASS if exc.category == "concurrent-change" else FAIL, f"category={exc.category}",
                     [] if exc.category == "concurrent-change" else ["the real 422 message does not contain 'fast forward': update github_api._error and the fake"])

    def item_4_edited_and_deleted(self) -> None:
        brain, src = self.brain2, self.src2
        tree = {e["path"]: e for e in self.api.tree(self.repo, self.api.commit(self.repo, self.api.head(self.repo, self.branch))["tree"]["sha"])}
        owned = next(p for p in tree if p.startswith("raw/p/") and p.endswith("a.md"))
        edited = self.api.blob(self.repo, tree[owned]["sha"]) + b"\nedited by a teammate in GitHub\n"
        self.other_commit({owned: edited}, "teammate edits an owned file")
        (src / "a.md").write_text("A3 changed at the source")
        res = sync_pipeline(brain, "p", trigger="cli")
        now = {e["path"]: e for e in self.api.tree(self.repo, self.api.commit(self.repo, self.api.head(self.repo, self.branch))["tree"]["sha"])}
        kept = self.api.blob(self.repo, now[owned]["sha"]) == edited
        self.add("4a", "an owned file edited in the repo is a conflict and is never overwritten", PASS if res.counts()["conflicts"] == 1 and kept else FAIL,
                 f"result={res.result} conflicts={res.counts()['conflicts']} edit kept={kept}")
        other = next(p for p in now if p.startswith("raw/p/") and p.endswith("e.md"))
        self.other_commit({other: None}, "teammate deletes an owned file")
        res = sync_pipeline(brain, "p", trigger="cli")
        back = {e["path"] for e in self.api.tree(self.repo, self.api.commit(self.repo, self.api.head(self.repo, self.branch))["tree"]["sha"])}
        self.add("4b", "an owned file deleted in the repo is recreated from the source (decision 1)", PASS if other in back else FAIL, f"result={res.result} recreated={other in back}")

    def item_5_limits(self) -> None:
        brain = self.new_brain("b5")
        self.make_pipeline(brain, "big", {f"f{n:03d}.md": f"file {n}" for n in range(self.scale)}, name_policy="original", guard_threshold_percent=100)
        started, requests = time.monotonic(), self.recorder.requests
        res = sync_pipeline(brain, "big", trigger="cli")
        elapsed, used = time.monotonic() - started, self.recorder.requests - requests
        remaining = [h.get("x-ratelimit-remaining") for h in self.recorder.headers if "x-ratelimit-remaining" in h]
        shape = [] if remaining else ["responses carry no x-ratelimit-remaining header (the client's rate-limit detection reads it)"]
        self.add("5a", f"a first sync of {self.scale} files", PASS if res.result == "ok" and not shape else FAIL,
                 f"result={res.result} {elapsed:.1f}s {used} requests; rate limit remaining after: {remaining[-1] if remaining else '?'}", shape)
        started, requests = time.monotonic(), self.recorder.requests
        res = sync_pipeline(brain, "big", trigger="cli", dry_run=True)
        self.add("5b", f"a dry run (read-only planning) of {self.scale} owned files", PASS if res.result == "ok" and res.counts()["unchanged"] == self.scale else FAIL,
                 f"{time.monotonic() - started:.1f}s {self.recorder.requests - requests} requests")
        self.add("5c", "tree listing limit (100,000 entries / 7 MB: truncated=true is an error, never 'files gone')", MANUAL, "cannot be created in a scratch repo cheaply; the code path is tested against the fake only")
        self.add("5d", "rate-limit response shape (403/429 with retry/x-ratelimit-remaining: 0)", MANUAL, "only visible when the limit is really hit; note the real headers if it happens")

    def item_6_clone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "clone"
            subprocess.run(["git", "init", "-q", str(clone)], check=True)
            subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", f"git@github.com:{self.repo}.git"], check=True)
            res = fast_forward_clone(str(clone), self.repo, self.branch)
            self.add("6a", "a clone whose origin is SSH is refused with a reason (https only)", PASS if res.status == "skipped" and "origin" in res.reason else FAIL, f"{res.status}: {res.reason}")
        self.add("6b", "an https clone is fast-forwarded after a sync", MANUAL,
                 f"git clone --branch {self.branch} https://github.com/{self.repo}.git ~/mdp-live-clone  (uses your own git credentials), then add the clone to a pipeline "
                 "(mdpipeline pipeline edit NAME --clone ~/mdp-live-clone), sync twice with a source change in between and check `git log` and the sync output: clone: updated")

    def cleanup(self) -> None:
        try:
            self.api.request("DELETE", f"/repos/{self.repo}/git/refs/heads/{self.branch}", what="the work branch")
            print(f"deleted the work branch {self.branch}")
        except TargetError as exc:
            print(f"could not delete the work branch {self.branch}: {exc}")
        shutil.rmtree(self.work_dir, ignore_errors=True)


def read_token(args) -> str:
    if args.use_secret:
        from mdpipeline import vault

        return vault.default_vault().get(args.use_secret)
    if args.stdin:
        return sys.stdin.readline().strip()
    return getpass.getpass("Token for the scratch repository (typing is hidden): ")


@contextlib.contextmanager
def no_scheduler_registration():
    """MD-169: a live check syncs scratch brains in temp folders. Registering them would put them in the REAL user's scheduler.json,
    where the background process then retried (and failed) them every hour. Undone when the script ends."""
    from mdpipeline import engine

    saved = engine.register_brain
    engine.register_brain = lambda brain: None
    try:
        yield
    finally:
        engine.register_brain = saved


def main(argv=None, transport=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, metavar="OWNER/SCRATCH")
    parser.add_argument("--scratch-repo-confirmed", action="store_true", help="I confirm this is a throw-away repository (it gets a work branch and test files)")
    parser.add_argument("--protected-branch", metavar="NAME")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--use-secret", metavar="ID", help="a token already in the app vault")
    parser.add_argument("--report", metavar="FILE", help="write the results as JSON")
    parser.add_argument("--files", type=int, default=500, help="size of the scale check (default 500)")
    parser.add_argument("--cleanup", action="store_true", help="delete the work branch at the end")
    args = parser.parse_args(argv)
    if not args.scratch_repo_confirmed:
        print("Refusing to run: pass --scratch-repo-confirmed to say this repository is a throw-away one (never the owner's brain repository).", file=sys.stderr)
        return 2
    checker = Checker(args.repo, read_token(args), args.protected_branch, transport, files_for_scale=args.files)
    try:
        with no_scheduler_registration():
            results = checker.run()
    finally:
        if args.cleanup:
            checker.cleanup()
        github_api.set_transport(None)
    if args.report:
        Path(args.report).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    failed = [r for r in results if r.status == FAIL]
    print(f"\n{sum(r.status == PASS for r in results)} PASS, {len(failed)} FAIL, {sum(r.status == MANUAL for r in results)} MANUAL; "
          f"{sum(len(r.divergences) for r in results)} divergence(s) between the fake and real GitHub")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
