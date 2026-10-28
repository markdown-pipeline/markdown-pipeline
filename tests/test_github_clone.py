"""MD-67: a local clone stays current for agents: fast-forward after each sync, never touch local work."""
import subprocess
from pathlib import Path

import pytest

from conftest import write
from mdpipeline import clone as clone_mod
from mdpipeline.clone import fast_forward_clone
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog


def git(cwd, *args):
    done = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """A bare 'GitHub' remote, a teammate's working copy that pushes, and the owner's clone whose origin is the https URL."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    mate = tmp_path / "mate"
    subprocess.run(["git", "clone", str(remote), str(mate)], check=True, capture_output=True)
    write(mate / "raw" / "a.md", "A")
    git(mate, "add", "."); git(mate, "commit", "-m", "init"); git(mate, "push", "origin", "HEAD:main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
    # the clone's origin is the https URL (the network rule); git is told where that URL really lives (the local bare repo)
    git(clone, "config", f"url.{remote}.insteadOf", "https://github.com/acme/brain.git")
    git(clone, "remote", "set-url", "origin", "https://github.com/acme/brain.git")

    def push(path, text, message="teammate"):
        write(mate / path, text)
        git(mate, "add", "."); git(mate, "commit", "-m", message); git(mate, "push", "origin", "HEAD:main")

    return type("R", (), {"remote": remote, "mate": mate, "clone": clone, "push": staticmethod(push)})


def test_a_clean_clone_is_fast_forwarded(repos):
    repos.push("raw/new.md", "N")
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status == "updated" and (repos.clone / "raw" / "new.md").read_text() == "N"


def test_an_up_to_date_clone_is_reported_as_such(repos):
    assert fast_forward_clone(str(repos.clone), "acme/brain", "main").status == "up-to-date"


def test_uncommitted_work_is_never_touched_and_is_reported(repos):
    repos.push("raw/new.md", "N")
    write(repos.clone / "raw" / "a.md", "my uncommitted edit")
    write(repos.clone / "scratch.md", "untracked")
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status == "skipped" and "local changes" in res.reason
    assert (repos.clone / "raw" / "a.md").read_text() == "my uncommitted edit" and (repos.clone / "scratch.md").exists()
    assert not (repos.clone / "raw" / "new.md").exists()


def test_local_commits_are_never_touched_a_diverged_clone_is_left_alone(repos):
    write(repos.clone / "local.md", "mine")
    git(repos.clone, "add", "."); git(repos.clone, "commit", "-m", "local work")
    local_head = git(repos.clone, "rev-parse", "HEAD")
    repos.push("raw/new.md", "N")
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status == "skipped" and "diverged" in res.reason
    assert git(repos.clone, "rev-parse", "HEAD") == local_head and not (repos.clone / "raw" / "new.md").exists()


def test_a_clone_that_is_only_ahead_is_left_alone(repos):
    write(repos.clone / "local.md", "mine")
    git(repos.clone, "add", "."); git(repos.clone, "commit", "-m", "local work")
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status in ("up-to-date", "skipped") and (repos.clone / "local.md").exists()


def test_a_clone_on_another_branch_is_left_alone(repos):
    git(repos.clone, "checkout", "-b", "feature")
    repos.push("raw/new.md", "N")
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status == "skipped" and "feature" in res.reason


def test_not_a_git_clone_and_missing_folder_are_reported(tmp_path):
    (tmp_path / "plain").mkdir()
    assert "not a git clone" in fast_forward_clone(str(tmp_path / "plain"), "acme/brain", "main").reason
    assert fast_forward_clone(str(tmp_path / "gone"), "acme/brain", "main").status == "skipped"


@pytest.mark.parametrize("url", ["git@github.com:acme/brain.git", "https://evil.example.com/acme/brain.git", "http://github.com/acme/brain.git",
                                 "https://github.com/other/thing.git"])
def test_only_an_https_origin_of_the_configured_repo_is_fetched(repos, url):
    git(repos.clone, "remote", "set-url", "origin", url)
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status == "skipped" and "origin" in res.reason


def test_a_failed_fetch_is_reported_without_touching_the_clone(repos):
    git(repos.clone, "config", f"url.{repos.remote}-missing.insteadOf", "https://github.com/acme/brain.git")
    git(repos.clone, "config", "--unset", f"url.{repos.remote}.insteadOf")
    res = fast_forward_clone(str(repos.clone), "acme/brain", "main")
    assert res.status == "skipped" and "fetch" in res.reason


def test_the_sync_fast_forwards_the_clone_and_reports_it(gh, repos, monkeypatch):
    gh.pipeline("p", {"a.md": "A"}, destination={**gh.destination, "clone": str(repos.clone)})
    seen = []
    monkeypatch.setattr("mdpipeline.engine.fast_forward_clone", lambda path, repo, branch: seen.append((path, repo, branch)) or clone_mod.CloneResult("updated", "", "abc"))
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert seen == [(str(repos.clone), "acme/brain", "main")]
    assert res.to_dict()["clone"]["status"] == "updated" and ActivityLog(gh.brain).read(types={"run"})[-1]["clone"]["status"] == "updated"


def test_a_skipped_clone_does_not_fail_the_sync(gh, repos, monkeypatch):
    gh.pipeline("p", {"a.md": "A"}, destination={**gh.destination, "clone": str(repos.clone)})
    write(repos.clone / "dirty.md", "x")
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.to_dict()["clone"]["status"] == "skipped" and "local changes" in res.to_dict()["clone"]["reason"]


def test_no_clone_configured_means_no_git_call(gh, monkeypatch):
    monkeypatch.setattr("mdpipeline.engine.fast_forward_clone", lambda *a: (_ for _ in ()).throw(AssertionError("git used")))
    gh.pipeline("p", {"a.md": "A"})
    assert sync_pipeline(gh.brain, "p", trigger="cli").result == "ok"


def test_status_reports_the_last_commit_and_the_clone_outcome(gh, repos):
    from mdpipeline.status import brain_status

    gh.pipeline("p", {"a.md": "A"}, destination={**gh.destination, "clone": str(repos.clone)})
    write(repos.clone / "dirty.md", "x")
    sync_pipeline(gh.brain, "p", trigger="cli")
    t = brain_status(gh.brain)["pipelines"][0]["target"]
    assert t["last_commit"]["sha"] == gh.fake.head() and t["last_commit"]["url"].endswith(gh.fake.head())
    assert t["clone_status"]["status"] == "skipped" and "local changes" in t["clone_status"]["reason"]


def test_cli_status_says_when_the_clone_was_not_updated(gh, repos, capsys):
    from mdpipeline.cli import main
    gh.pipeline("p", {"a.md": "A"}, destination={**gh.destination, "clone": str(repos.clone)})
    write(repos.clone / "dirty.md", "x")
    sync_pipeline(gh.brain, "p", trigger="cli")
    capsys.readouterr()
    main(["--brain", str(gh.brain.root), "status"])
    out = capsys.readouterr().out
    assert "clone: NOT updated" in out and "local changes" in out and "last commit: https://github.com/acme/brain/commit/" in out
