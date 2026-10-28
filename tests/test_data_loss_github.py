"""MD-69: the GitHub target data-loss suite (P3 release gate). Run alone with:

    uv run pytest tests/test_data_loss_github.py

Scenario numbers match GT-S9 / the acceptance criteria of MD-69. Everything runs against the fake GitHub: no real network.
Together with tests/test_data_loss.py (MD-30) and tests/test_data_loss_names.py (MD-41) this is the gate.
"""
import json
import socket

import pytest

from conftest import write
from mdpipeline import github_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import MAX_ATTEMPTS, sync_pipeline
from mdpipeline.github_target import STATE_PATH
from mdpipeline.recovery import apply_restore
from mdpipeline.state import StateError


class Kill(BaseException):
    """The process is killed (not caught by `except Exception`)."""


def allowed(path):
    return path == STATE_PATH or (path.startswith("raw/") and path.endswith(".md") and ".." not in path.split("/"))


def fresh(gh):
    """A new process: the same control directory, nothing cached."""
    gh.brain._cache.clear()


@pytest.fixture
def world(gh):
    gh.fake.commit_as_other({"wiki/index.md": b"curated", "raw/hand/note.md": b"hand written", "docs/guide.md": b"guide"}, "owner's own files")
    gh.src = gh.pipeline("p", {"a.md": "A", "sub/b.md": "B", "c.md": "C"}, guard_threshold_percent=40, guard_min_files=5)
    gh.outside = {p: d for p, d in gh.fake.files().items()}
    return gh


def untouched_outside(w):
    """wiki/, hand-written raw files and other folders are byte-identical to the owner's."""
    now = w.fake.files()
    for path in ("wiki/index.md", "raw/hand/note.md", "docs/guide.md", "README.md"):
        assert now[path] == w.outside[path], path


# 1
@pytest.mark.parametrize("step", ["/git/blobs", "/git/trees", "/git/commits", "/git/refs/"])
def test_01_network_failure_mid_sync_leaves_the_branch_unchanged_and_the_next_run_converges(world, step):
    head = world.fake.head()
    world.fake.fail("PATCH" if step == "/git/refs/" else "POST", step, status=502, times=1)
    assert sync_pipeline(world.brain, "p", trigger="cli").result == "error" and world.fake.head() == head
    assert sync_pipeline(world.brain, "p", trigger="cli").result == "ok"
    untouched_outside(world)
    assert sync_pipeline(world.brain, "p", trigger="cli").commit is None


# 2
def test_02_auth_failure_leaves_the_branch_unchanged_and_needs_you_shows_it(world, monkeypatch):
    from uihelper import running_ui

    head = world.fake.head()
    monkeypatch.setenv("BRAIN_TOKEN", "ghp_WRONGWRONGWRONG")
    fresh(world)
    res = sync_pipeline(world.brain, "p", trigger="ui")
    assert res.result == "error" and res.category == "auth" and world.fake.head() == head
    with running_ui(world.brain) as ui:
        assert any(c["kind"] == "target" and c["category"] == "auth" for c in ui.get("/api/activity")[1]["needs_you"])


# 3
def test_03_the_head_moves_during_a_sync_the_other_commit_is_kept_and_ours_goes_on_top(world):
    world.fake.before("PATCH", lambda: world.fake.commit_as_other({"wiki/new.md": b"teammate"}, "teammate"), times=1)
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "ok" and world.fake.read("wiki/new.md") == b"teammate" and world.fake.is_linear_history()
    untouched_outside(world)


# 4
def test_04_the_head_moves_on_every_retry_the_sync_stops_cleanly_and_writes_nothing(world):
    n = []
    world.fake.before("PATCH", lambda: n.append(world.fake.commit_as_other({f"wiki/m{len(n)}.md": b"x"}, "busy")), times=99)
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "head-moved" and len(n) == MAX_ATTEMPTS
    assert world.fake.read(STATE_PATH) is None and not [p for p in world.fake.files() if p.startswith("raw/p/")]


# 5
def test_05_someone_edits_an_owned_raw_file_it_is_a_conflict_and_never_overwritten(world):
    sync_pipeline(world.brain, "p", trigger="cli")
    world.fake.commit_as_other({"raw/p/p/a.md": b"edited by a teammate"}, "edit")
    (world.src / "a.md").write_text("A changed at the source")
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "conflicts" and world.fake.read("raw/p/p/a.md") == b"edited by a teammate"


# 6
def test_06_a_hand_written_file_at_a_target_path_is_never_claimed(world):
    world.fake.commit_as_other({"raw/p/p/a.md": b"my own note"}, "hand")
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "conflicts" and world.fake.read("raw/p/p/a.md") == b"my own note"
    assert "raw/p/p/a.md" not in json.loads(world.fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    assert world.fake.read("raw/p/p/c.md") is not None            # the other files still synced


# 7
def test_07_wiki_changes_in_the_same_window_are_untouched(world):
    world.fake.before("PATCH", lambda: world.fake.commit_as_other({"wiki/index.md": b"curated, edited meanwhile", "wiki/x.md": b"x"}, "wiki edit"), times=1)
    sync_pipeline(world.brain, "p", trigger="cli")
    assert world.fake.read("wiki/index.md") == b"curated, edited meanwhile" and world.fake.read("wiki/x.md") == b"x"


# 8
def test_08_guard_blocked_makes_no_commit_and_approval_commits_exactly_the_shown_plan(world):
    big = world.pipeline("big", {f"n{i}.md": str(i) for i in range(10)})
    sync_pipeline(world.brain, "big", trigger="cli")
    for i in range(8):
        (big / f"n{i}.md").unlink()
    head = world.fake.head()
    blocked = sync_pipeline(world.brain, "big", trigger="cli")
    assert blocked.result == "blocked" and world.fake.head() == head
    shown = sorted(f["target"] for f in blocked.to_dict()["affected_files"])
    assert sync_pipeline(world.brain, "big", trigger="mcp", approve=True).result == "blocked" and world.fake.head() == head
    before = len(world.fake.history())
    assert sync_pipeline(world.brain, "big", trigger="cli", approve=True).result == "ok"
    assert len(world.fake.history()) == before + 1
    assert sorted(p for p, d in world.fake.commit_files_changed(world.fake.head()).items() if d is None) == shown


# 9
def test_09_a_policy_change_bulk_rename_is_one_commit_with_no_duplicates(world):
    sync_pipeline(world.brain, "p", trigger="cli")
    cfg = load_config(world.brain)
    cfg.get("p").name_policy = "date-name"
    cfg.get("p").guard_threshold_percent = 100
    save_config(world.brain, cfg)
    before = len(world.fake.history())
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["renamed"] == 3 and len(world.fake.history()) == before + 1
    raw = sorted(p for p in world.fake.files() if p.startswith("raw/p/"))
    assert len(raw) == 3 and not any(p.endswith(("/a.md", "/c.md")) and "20" not in p.split("/")[-1] for p in raw)
    assert sync_pipeline(world.brain, "p", trigger="cli").commit is None


# 10
@pytest.mark.parametrize("when", ["before-blobs", "before-tree", "before-commit", "before-ref"])
def test_10_sigkill_before_the_ref_moves_leaves_no_partial_commit_and_converges(world, when):
    head = world.fake.head()
    method, contains = {"before-blobs": ("POST", "/git/blobs"), "before-tree": ("POST", "/git/trees"),
                        "before-commit": ("POST", "/git/commits"), "before-ref": ("PATCH", "/git/refs/")}[when]

    def kill():
        raise Kill()

    world.fake.before(method, kill, times=1, path_contains=contains)
    with pytest.raises(Kill):
        sync_pipeline(world.brain, "p", trigger="cli")
    assert world.fake.head() == head
    fresh(world)
    from mdpipeline.locking import is_locked

    assert not is_locked(world.brain)
    assert sync_pipeline(world.brain, "p", trigger="cli").counts()["created"] == 3
    untouched_outside(world)


def test_10b_sigkill_right_after_the_ref_moved_the_next_run_has_nothing_to_do(world):
    from mdpipeline import engine

    def die(*a, **k):
        raise Kill()

    original = engine.ActivityLog.append
    engine.ActivityLog.append = die                              # the process dies after the commit, before it logs
    try:
        with pytest.raises(Kill):
            sync_pipeline(world.brain, "p", trigger="cli")
    finally:
        engine.ActivityLog.append = original
    assert world.fake.read("raw/p/p/a.md") is not None
    fresh(world)
    again = sync_pipeline(world.brain, "p", trigger="cli")
    assert again.commit is None and again.counts()["unchanged"] == 3


# 11
def test_11_idempotence_the_second_sync_makes_zero_commits(world):
    sync_pipeline(world.brain, "p", trigger="cli")
    n, seen = len(world.fake.history()), len(world.fake.requests)
    for _ in range(3):
        assert sync_pipeline(world.brain, "p", trigger="cli").commit is None
    assert len(world.fake.history()) == n and not [r for r in world.fake.requests[seen:] if r[0] in ("POST", "PATCH")]


# 12
def test_12_fast_forward_only_the_ref_history_is_never_rewritten(world):
    first = world.fake.history()
    sync_pipeline(world.brain, "p", trigger="cli")
    (world.src / "a.md").write_text("A2")
    world.fake.before("PATCH", lambda: world.fake.commit_as_other({"wiki/z.md": b"z"}, "race"), times=1)
    sync_pipeline(world.brain, "p", trigger="cli")
    assert world.fake.forced_updates == 0 and world.fake.is_linear_history() and world.fake.history()[: len(first)] == first
    assert all(not c["body"].get("force") for c in world.fake.log if c["method"] == "PATCH")


# 13
def test_13_corrupted_state_in_the_repo_is_restored_from_an_earlier_commit_without_losing_or_claiming_files(world):
    sync_pipeline(world.brain, "p", trigger="cli")
    good = world.fake.read(STATE_PATH)
    world.fake.commit_as_other({STATE_PATH: b"\x00\x01 garbage"}, "corrupted")
    with pytest.raises(StateError):
        sync_pipeline(world.brain, "p", trigger="cli")
    raw_before = {p: d for p, d in world.fake.files().items() if p.startswith("raw/")}
    apply_restore(world.brain)
    assert world.fake.read(STATE_PATH) == good
    assert {p: d for p, d in world.fake.files().items() if p.startswith("raw/")} == raw_before
    assert "raw/hand/note.md" not in json.loads(good)["pipelines"]["p"]["files"]
    assert sync_pipeline(world.brain, "p", trigger="cli").counts()["unchanged"] == 3


# 14
def test_14_the_token_is_nowhere_in_the_history_or_any_output(world, capsys, monkeypatch):
    def cli(*argv):
        main(["--brain", str(world.brain.root), *argv])
        out = capsys.readouterr()
        return out.out + out.err

    outputs = [cli("target", "test"), cli("sync", "p", "--dry-run"), cli("sync", "p"), cli("status", "--json"), cli("log", "--json"), cli("pipeline", "list")]
    monkeypatch.setenv("BRAIN_TOKEN", "ghp_WRONGWRONGWRONG")
    fresh(world)
    outputs.append(cli("sync", "p"))
    monkeypatch.setenv("BRAIN_TOKEN", world.token)
    fresh(world)
    haystack = [b.decode(errors="replace") for b in world.fake._blobs.values()] + [c["message"] for c in world.fake._commits.values()] + outputs
    for path in world.brain.root.rglob("*"):
        if path.is_file():
            haystack.append(path.read_text(errors="replace"))
    assert not any(world.token in h or "ghp_WRONG" in h for h in haystack)


# 15
@pytest.mark.parametrize("template", ["../evil/{relpath}", "raw/../wiki/{relpath}", "wiki/{relpath}", ".mdpipeline/{relpath}", ".github/{relpath}"])      # MD-133: root and docs/ are allowed now
def test_15_a_hostile_template_never_writes_outside_raw(world, template):
    world.pipeline("evil", {"a.md": "A"}, target=template)
    head = world.fake.head()
    assert sync_pipeline(world.brain, "evil", trigger="cli").result == "error"
    assert world.fake.head() == head
    untouched_outside(world)


def test_15b_hostile_labels_and_pipeline_names_never_write_outside_raw(world):
    src = world.tmp / "sources" / "h"
    write(src / "a.md", "A")
    cfg = load_config(world.brain)
    cfg.pipelines += [Pipeline.new("../wiki", [Source(str(src), "docs")]), Pipeline.new("q", [Source(str(src), "../../wiki")])]
    save_config(world.brain, cfg)
    before = len(world.fake.history())
    for name in ("../wiki", "q"):
        sync_pipeline(world.brain, name, trigger="cli")
    for sha in world.fake.history()[before:]:
        assert all(allowed(p) for p in world.fake.commit_files_changed(sha))
    untouched_outside(world)


# 16 (the move of a local pipeline to GitHub is MD-85: tests/test_change_target.py)


# 17
def test_17_local_only_brains_make_zero_network_calls(brain, add_pipeline, make_source, monkeypatch):
    calls = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a))
    github_api.set_transport(lambda *a: calls.append(a))
    try:
        add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
        assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
        assert main(["--brain", str(brain.root), "status"]) == 0
    finally:
        github_api.set_transport(None)
    assert calls == []
