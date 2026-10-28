"""MD-85: change a pipeline's target (directory <-> GitHub, or another repo): the plan first, the old target never touched, names carry over."""
import json

import pytest

from conftest import tree_snapshot, write
from fake_github import FakeGitHub
from mdpipeline import github_api, vault as vault_mod
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.github_target import STATE_PATH
from mdpipeline.retarget import ChangeRefused, apply_change, preview_change
from mdpipeline.state import State
from mdpipeline.targetconfig import GitHubSpec

TOKEN = "ghp_CHANGETARGETSECRET0123"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


@pytest.fixture
def local(brain, brain_dir, make_source, add_pipeline):
    """A pipeline in the brain's own folder that already synced (date-name policy: names carry first-seen dates)."""
    src = make_source("docs", {"a.md": "A", "sub/b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    return type("L", (), {"brain": brain, "src": src, "root": brain_dir})


def github_dest(secret_id, **extra):
    return GitHubSpec("acme/brain", f"vault:{secret_id}", "main").to_dict() | {"auth": "token"} | extra


def push_owned_files(local, fake):
    """The owner pushes the brain folder to the repository (their own git push), as for the old brain-level move."""
    fake.commit_as_other({p.relative_to(local.root).as_posix(): p.read_bytes() for p in (local.root / "raw").rglob("*.md")}, "owner pushes the brain")


def test_the_plan_for_the_new_target_shows_zero_changes_when_the_files_are_already_there(local, fake):
    push_owned_files(local, fake)
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    preview = preview_change(local.brain, "p", github_dest(secret))
    c = preview.plan.counts()
    assert (c["created"], c["updated"], c["renamed"], c["deleted"], c["conflicts"]) == (0, 0, 0, 0, 0) and c["unchanged"] == 2
    assert preview.owned_files == 2 and preview.missing == [] and preview.differing == [] and preview.applied is False


def test_a_preview_writes_nothing_anywhere(local, fake):
    push_owned_files(local, fake)
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    before_local, head, config = tree_snapshot(local.root), fake.head(), local.brain.config_path.read_text()
    preview_change(local.brain, "p", github_dest(secret))
    assert tree_snapshot(local.root) == before_local and fake.head() == head and local.brain.config_path.read_text() == config


def test_files_not_yet_in_the_new_target_are_listed_and_would_be_created_by_the_next_sync(local, fake):
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    preview = preview_change(local.brain, "p", github_dest(secret))
    assert len(preview.missing) == 2 and preview.plan.counts()["created"] == 2


def test_apply_moves_ownership_names_and_dates_unchanged_and_leaves_the_old_target_byte_identical(local, fake):
    from mdpipeline.state import _record_dict

    push_owned_files(local, fake)
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    old_state = State.load(local.brain).pipelines["p"]
    before = tree_snapshot(local.root / "raw")
    res = apply_change(local.brain, "p", github_dest(secret))
    assert res.applied and res.stays_behind == 2
    assert tree_snapshot(local.root / "raw") == before                                  # the old target's files: byte- and mtime-identical
    moved = json.loads(fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    assert moved == {t: _record_dict(r) for t, r in old_state.files.items()}            # ownership, first-seen dates, policy and suffixes carried over
    dest = load_config(local.brain).get("p").destination
    assert dest["type"] == "github" and dest["repo"] == "acme/brain"
    c = sync_pipeline(local.brain, "p", trigger="cli", dry_run=True).counts()
    assert (c["created"], c["updated"], c["renamed"], c["deleted"], c["conflicts"]) == (0, 0, 0, 0, 0) and c["unchanged"] == 2


def test_the_old_target_state_file_is_not_touched_either(local, fake):
    push_owned_files(local, fake)
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    state_bytes = (local.root / ".mdpipeline/state.json").read_bytes()
    apply_change(local.brain, "p", github_dest(secret))
    assert (local.root / ".mdpipeline/state.json").read_bytes() == state_bytes


def test_apply_commits_only_the_state_to_the_new_repository(local, fake):
    push_owned_files(local, fake)
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    apply_change(local.brain, "p", github_dest(secret))
    changed = fake.commit_files_changed(fake.head())
    assert set(changed) == {STATE_PATH} and "change target" in fake.commit_message(fake.head())


def test_a_move_to_another_directory_carries_the_ownership_there(local, tmp_path):
    from mdpipeline.destinations import bind_target

    other = tmp_path / "vault"
    for p in (local.root / "raw").rglob("*.md"):
        write(other / p.relative_to(local.root), p.read_text())
    before = tree_snapshot(local.root / "raw")
    res = apply_change(local.brain, "p", {"type": "directory", "path": str(other)})
    assert res.applied and tree_snapshot(local.root / "raw") == before
    assert len(State.load(bind_target(local.brain, load_config(local.brain).get("p"))).pipelines["p"].files) == 2
    c = sync_pipeline(local.brain, "p", trigger="cli", dry_run=True).counts()
    assert (c["created"], c["updated"], c["renamed"], c["deleted"]) == (0, 0, 0, 0) and c["unchanged"] == 2


def test_changing_to_the_same_target_is_refused(local):
    with pytest.raises(ChangeRefused) as exc:
        apply_change(local.brain, "p", {"type": "directory", "path": str(local.root)})
    assert "already" in str(exc.value)


def test_a_target_that_holds_different_ownership_records_for_that_pipeline_is_refused_and_unchanged(local, fake):
    other_state = {"version": 1, "pipelines": {"p": {"files": {"raw/p/old.md": {"source_label": "x", "source_path": "old.md", "source_hash": "h", "written_hash": "w"}}}}}
    fake.commit_as_other({STATE_PATH: json.dumps(other_state).encode()}, "an earlier state")
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    head, config = fake.head(), local.brain.config_path.read_text()
    with pytest.raises(ChangeRefused) as exc:
        apply_change(local.brain, "p", github_dest(secret))
    assert "ownership" in str(exc.value) and fake.head() == head and local.brain.config_path.read_text() == config


def test_a_new_target_that_cannot_be_read_changes_nothing(local, fake):
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    fake.fail("GET", "/git/ref/heads", status=502, times=99)
    config = local.brain.config_path.read_text()
    with pytest.raises(Exception):
        apply_change(local.brain, "p", github_dest(secret))
    assert local.brain.config_path.read_text() == config and fake.read(STATE_PATH) is None


def test_files_the_new_target_holds_with_other_bytes_are_reported_and_never_overwritten_by_the_move(local, fake):
    push_owned_files(local, fake)
    path = next(p for p in fake.files() if p.startswith("raw/") and p.endswith("a.md"))
    fake.commit_as_other({path: b"someone edited it"}, "edit")
    secret = vault_mod.default_vault().add("github-token", "t", TOKEN)
    preview = preview_change(local.brain, "p", github_dest(secret))
    assert preview.differing == [path] and preview.plan.counts()["conflicts"] == 1
    apply_change(local.brain, "p", github_dest(secret))
    assert fake.read(path) == b"someone edited it"
