"""MD-64: state lives in the repo (same commit as content, no content or secrets), any machine can continue, recovery from git history."""
import json

import pytest

from conftest import write
from mdpipeline.brain import find_brain, init_brain
from mdpipeline.cli import main
from mdpipeline.config import load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.github_target import STATE_PATH
from mdpipeline.rebuild import apply_rebuild, rebuild_preview
from mdpipeline.recovery import RestoreError, apply_restore, preview_restore
from mdpipeline.state import State, StateError
from mdpipeline.targetconfig import GitHubSpec


def second_machine(gh, name="machine2"):
    """A fresh control directory with the same configuration (target, token reference, pipelines) and nothing else."""
    brain = init_brain(gh.tmp / name, create=True)
    save_config(brain, load_config(gh.brain))
    return brain


def test_state_is_in_the_repo_in_the_same_commit_as_the_content(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    changed = gh.fake.commit_files_changed(gh.fake.head())
    assert STATE_PATH in changed and "raw/p/p/a.md" in changed
    assert not (gh.brain.config_dir / "state.json").exists()                   # not kept locally


def test_state_holds_no_file_content_and_no_secrets(gh):
    gh.pipeline("p", {"a.md": "UNIQUE-CONTENT-MARKER-12345 body text"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    raw = gh.fake.read(STATE_PATH).decode()
    assert "UNIQUE-CONTENT-MARKER" not in raw and gh.token not in raw and "BRAIN_TOKEN" not in raw
    files = json.loads(raw)["pipelines"]["p"]["files"]["raw/p/p/a.md"]
    assert set(files) <= {"source_label", "source_path", "source_hash", "written_hash", "pending_hash", "first_seen", "name_policy", "suffix"}


def test_a_second_machine_continues_with_a_zero_change_plan(gh):
    gh.pipeline("p", {"a.md": "A", "b.md": "B"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    other = second_machine(gh)
    res = sync_pipeline(other, "p", trigger="cli", dry_run=True)
    assert res.counts()["created"] == res.counts()["updated"] == res.counts()["renamed"] == res.counts()["deleted"] == 0
    assert res.counts()["unchanged"] == 2 and res.counts()["conflicts"] == 0
    head = gh.fake.head()
    assert sync_pipeline(other, "p", trigger="cli").commit is None and gh.fake.head() == head


def test_naming_fields_survive_a_move_between_machines(gh):
    gh.pipeline("p", {"a.md": "A", "sub/a.md": "A2"}, name_policy="date-name")
    sync_pipeline(gh.brain, "p", trigger="cli")
    before = json.loads(gh.fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    other = second_machine(gh)
    sync_pipeline(other, "p", trigger="cli")
    after = json.loads(gh.fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    assert after == before and all(r["first_seen"] and r["name_policy"] for r in after.values())


def corrupt_state(gh):
    gh.fake.commit_as_other({STATE_PATH: b"{ this is not json"}, "oops")


def test_corrupt_state_stops_the_sync_and_names_the_recovery(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    corrupt_state(gh)
    head = gh.fake.head()
    with pytest.raises(StateError) as exc:
        sync_pipeline(gh.brain, "p", trigger="cli")
    assert "state restore" in str(exc.value) and gh.fake.head() == head


def test_restore_previews_first_then_commits_the_state_from_an_earlier_commit(gh):
    src = gh.pipeline("p", {"a.md": "A", "b.md": "B"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    good = gh.fake.read(STATE_PATH)
    corrupt_state(gh)
    preview = preview_restore(gh.brain)
    assert preview.owned_files == 2 and preview.plans["p"].counts()["unchanged"] == 2 and not preview.applied
    assert gh.fake.read(STATE_PATH) == b"{ this is not json"                       # the preview wrote nothing
    apply_restore(gh.brain)
    assert gh.fake.read(STATE_PATH) == good
    assert gh.fake.commit_message(gh.fake.head()).startswith("mdpipeline restore state")
    assert sync_pipeline(gh.brain, "p", trigger="cli").counts()["unchanged"] == 2 and gh.fake.is_linear_history()


def test_restore_with_no_usable_earlier_state_says_so(gh):
    gh.pipeline("p", {"a.md": "A"})
    corrupt_state(gh)
    with pytest.raises(RestoreError):
        preview_restore(gh.brain)


def test_restore_never_claims_a_hand_written_file(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    corrupt_state(gh)
    gh.fake.commit_as_other({"raw/hand/note.md": b"mine"}, "hand")
    apply_restore(gh.brain)
    assert "raw/hand/note.md" not in json.loads(gh.fake.read(STATE_PATH))["pipelines"]["p"]["files"]


def test_cli_restore_for_a_github_brain(gh, capsys):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    corrupt_state(gh)
    assert main(["--brain", str(gh.brain.root), "state", "restore"]) == 0
    assert "Nothing was changed" in capsys.readouterr().out and gh.fake.read(STATE_PATH) == b"{ this is not json"
    assert main(["--brain", str(gh.brain.root), "state", "restore", "--apply"]) == 0
    assert json.loads(gh.fake.read(STATE_PATH))["pipelines"]["p"]["files"]


def test_rebuild_from_provenance_works_against_the_repos_raw(gh):
    gh.pipeline("p", {"a.md": "A", "b.md": "B"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    gh.fake.commit_as_other({STATE_PATH: None}, "state lost")
    gh.fake.commit_as_other({"raw/hand/note.md": b"hand written"}, "note")
    preview = rebuild_preview(gh.brain)
    assert preview.claimed_total == 2
    apply_rebuild(gh.brain)
    state = json.loads(gh.fake.read(STATE_PATH))["pipelines"]["p"]["files"]
    assert sorted(state) == ["raw/p/p/a.md", "raw/p/p/b.md"] and "raw/hand/note.md" not in state
    assert gh.fake.commit_message(gh.fake.head()).startswith("mdpipeline rebuild state")
    assert sync_pipeline(gh.brain, "p", trigger="cli").counts()["unchanged"] == 2
