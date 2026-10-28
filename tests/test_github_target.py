"""MD-61/62/63: the GitHub target against the fake GitHub: reads, one atomic commit, races, failures."""
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline.github_api import GitHubApi
from mdpipeline.github_target import STATE_PATH, GitHubTarget
from mdpipeline.target import ConcurrentChange, FileRead, TargetAuthError, TargetError, TargetUnavailable
from mdpipeline.writer import WriteRefused

TOKEN = "ghp_SECRETSECRETSECRET"


@pytest.fixture
def fake():
    return FakeGitHub(repo="acme/brain", branch="main", token=TOKEN,
                      files={"raw/a.md": b"A", "raw/sub/b.md": b"B", "wiki/w.md": b"W", STATE_PATH: b"{}"})


@pytest.fixture
def target(fake):
    return GitHubTarget(GitHubApi(TOKEN, transport=fake), "acme/brain", "main")


def test_reads_files_absent_and_state(target):
    target.begin()
    assert target.read_file("raw/a.md") == FileRead("file", b"A")
    assert target.read_file("raw/none.md") == FileRead("absent", None)
    assert target.read_state() == b"{}"


def test_a_path_that_is_a_folder_is_other(target):
    target.begin()
    assert target.read_file("raw/sub").kind == "other"


def test_list_markdown_only_lists_raw(target):
    target.begin()
    assert target.list_markdown() == ["raw/a.md", "raw/sub/b.md"]


def test_missing_state_is_none(fake):
    fake.commit_as_other({STATE_PATH: None})
    t = GitHubTarget(GitHubApi(TOKEN, transport=fake), "acme/brain", "main")
    t.begin()
    assert t.read_state() is None


def test_a_failed_read_raises_and_is_never_absent(target, fake):
    target.begin()
    fake.fail("GET", "/git/blobs/", status=500, times=1)
    with pytest.raises(TargetUnavailable):
        target.read_file("raw/a.md")


def test_auth_failure_at_begin_raises(fake):
    t = GitHubTarget(GitHubApi("ghp_WRONG", transport=fake), "acme/brain", "main")
    with pytest.raises(TargetAuthError):
        t.begin()


def test_blobs_are_cached_across_operations(target, fake):
    target.begin()
    target.read_file("raw/a.md")
    n = sum(1 for m, p in fake.requests if "/git/blobs/" in p)
    target.begin()
    target.read_file("raw/a.md")
    assert sum(1 for m, p in fake.requests if "/git/blobs/" in p) == n


def test_staged_changes_are_visible_to_reads_but_not_to_the_repo(target, fake):
    target.begin()
    target.write("raw/new.md", b"N")
    target.delete("raw/a.md")
    assert target.read_file("raw/new.md") == FileRead("file", b"N") and target.read_file("raw/a.md").kind == "absent"
    assert fake.read("raw/new.md") is None and fake.read("raw/a.md") == b"A"


def test_commit_is_one_commit_with_everything_and_fast_forward(target, fake):
    before = fake.head()
    target.begin()
    target.write("raw/new.md", b"N")
    target.write("raw/a.md", b"A2")
    target.delete("raw/sub/b.md")
    target.write_state(b'{"v": 2}')
    info = target.commit("mdpipeline sync [p]: 1 created")
    assert fake.history()[-2] == before and len(fake.history()) == 2
    assert fake.commit_files_changed(fake.head()) == {"raw/new.md": b"N", "raw/a.md": b"A2", "raw/sub/b.md": None, STATE_PATH: b'{"v": 2}'}
    assert fake.commit_message(fake.head()).startswith("mdpipeline sync [p]")
    assert info.sha == fake.head() and info.url.endswith(info.sha) and fake.forced_updates == 0
    assert fake.read("wiki/w.md") == b"W"                       # everything else carried over


def test_commit_with_no_net_change_creates_nothing(target, fake):
    before = fake.head()
    target.begin()
    target.write("raw/a.md", b"A")                              # same bytes as at the head
    target.write_state(b"{}")
    assert target.commit("m") is None and fake.head() == before
    assert not [r for r in fake.requests if r[0] in ("POST", "PATCH")]


def test_deleting_a_missing_file_is_not_a_change(target, fake):
    target.begin()
    target.delete("raw/never-there.md")
    assert target.commit("m") is None


def test_branch_moved_means_concurrent_change_and_nothing_written(target, fake):
    target.begin()
    target.write("raw/new.md", b"N")
    fake.before("PATCH", lambda: fake.commit_as_other({"wiki/other.md": b"o"}, "teammate"))
    with pytest.raises(ConcurrentChange):
        target.commit("m")
    assert fake.read("raw/new.md") is None and fake.read("wiki/other.md") == b"o" and fake.forced_updates == 0


@pytest.mark.parametrize("step", ["/git/blobs", "/git/trees", "/git/commits"])
def test_failure_at_any_step_leaves_the_branch_unchanged(target, fake, step):
    before = fake.head()
    target.begin()
    target.write("raw/new.md", b"N")
    fake.fail("POST", step, status=502, times=1)
    with pytest.raises(TargetUnavailable):
        target.commit("m")
    assert fake.head() == before
    target.begin()                      # the next operation converges
    target.write("raw/new.md", b"N")
    assert target.commit("m") is not None and fake.read("raw/new.md") == b"N"


def test_rate_limit_during_commit_leaves_branch_unchanged(target, fake):
    before = fake.head()
    target.begin()
    target.write("raw/new.md", b"N")
    fake.before("PATCH", lambda: fake.rate_limit(times=1))
    with pytest.raises(TargetError):
        target.commit("m")
    assert fake.head() == before


@pytest.mark.parametrize("rel", ["wiki/x.md", "raw/../wiki/x.md", "raw/x.txt", "/raw/x.md", ".mdpipeline/state.json", ".git/x.md", "Wiki/x.md"])
def test_target_itself_refuses_protected_and_hostile_paths(target, fake, rel):
    target.begin()
    with pytest.raises(WriteRefused):
        target.write(rel, b"x")
    with pytest.raises(WriteRefused):
        target.delete(rel)


def test_the_state_path_is_the_only_extra_path_a_commit_can_touch(target, fake):
    target.begin()
    target.write("raw/n.md", b"N")
    target.write_state(b"{}2")
    target.commit("m")
    assert set(fake.commit_files_changed(fake.head())) == {"raw/n.md", STATE_PATH}


def test_reads_after_commit_see_the_new_head_without_a_refetch(target, fake):
    target.begin()
    target.write("raw/n.md", b"N")
    target.commit("m")
    n = len(fake.requests)
    assert target.read_file("raw/n.md") == FileRead("file", b"N")
    assert len(fake.requests) == n


def test_a_symlink_or_submodule_in_the_repo_is_other(fake):
    sha = fake.commit_as_other({"raw/link.md": b"target"})
    fake._trees[fake._commits[sha]["tree"]]["raw/link.md"] = ("120000", fake._trees[fake._commits[sha]["tree"]]["raw/link.md"][1])
    t = GitHubTarget(GitHubApi(TOKEN, transport=fake), "acme/brain", "main")
    t.begin()
    assert t.read_file("raw/link.md").kind == "other"
