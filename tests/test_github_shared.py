"""MD-63: other people's and agents' commits are never lost; repo edits to owned files are brain edits."""
import json

from conftest import write
from mdpipeline.engine import MAX_ATTEMPTS, sync_pipeline
from mdpipeline.github_target import STATE_PATH


def test_a_commit_made_during_the_sync_is_kept_and_ours_goes_on_top(gh):
    gh.pipeline("p", {"a.md": "A"})
    gh.fake.before("PATCH", lambda: gh.fake.commit_as_other({"wiki/new.md": b"teammate"}, "teammate"), times=1)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.commit is not None
    assert gh.fake.read("wiki/new.md") == b"teammate" and gh.fake.read("raw/p/p/a.md") is not None
    assert gh.fake.is_linear_history() and gh.fake.forced_updates == 0
    msgs = [gh.fake.commit_message(s) for s in gh.fake.history()]
    assert "teammate" in msgs and msgs[-1].startswith("mdpipeline sync [p]")


def test_when_the_head_moves_on_every_retry_the_sync_stops_cleanly_and_writes_nothing(gh):
    gh.pipeline("p", {"a.md": "A"})
    moves = []
    gh.fake.before("PATCH", lambda: moves.append(gh.fake.commit_as_other({f"wiki/m{len(moves)}.md": b"x"}, "busy teammate")), times=99)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "head-moved" and "nothing was written" in res.message
    assert len(moves) == MAX_ATTEMPTS
    assert gh.fake.read("raw/p/p/a.md") is None and gh.fake.read(STATE_PATH) is None
    assert gh.fake.forced_updates == 0 and len(gh.fake.history()) == 1 + len(moves)      # only the other party's commits


def test_files_outside_the_change_set_are_carried_over_exactly(gh):
    gh.fake.commit_as_other({"raw/hand/note.md": b"mine", "docs/guide.md": b"G", "wiki/w.md": b"W2"}, "setup")
    before = gh.fake.files()
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    after = gh.fake.files()
    for path, data in before.items():
        assert after[path] == data
    assert set(after) - set(before) == {"raw/p/p/a.md", STATE_PATH}


def test_a_hand_written_file_at_a_target_path_is_never_claimed_or_overwritten(gh):
    gh.fake.commit_as_other({"raw/p/p/a.md": b"hand written"}, "someone")
    gh.pipeline("p", {"a.md": "A"})
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "conflicts" and res.conflicts[0]["type"] == "a" and gh.fake.read("raw/p/p/a.md") == b"hand written"
    state = json.loads(gh.fake.read(STATE_PATH) or b'{"pipelines": {}}')["pipelines"].get("p", {"files": {}})
    assert "raw/p/p/a.md" not in state["files"]


def test_an_owned_file_edited_in_the_repo_is_a_brain_edit_conflict_never_overwritten(gh):
    src = gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    gh.fake.commit_as_other({"raw/p/p/a.md": b"edited by a teammate"}, "edit")
    (src / "a.md").write_text("A changed at the source")
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "conflicts" and res.conflicts[0]["type"] == "b"
    assert gh.fake.read("raw/p/p/a.md") == b"edited by a teammate"


def test_an_edit_made_by_someone_during_the_sync_is_seen_on_the_retry_as_a_conflict(gh):
    src = gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    (src / "a.md").write_text("A changed at the source")
    gh.fake.before("PATCH", lambda: gh.fake.commit_as_other({"raw/p/p/a.md": b"edited during the sync"}, "race"), times=1)
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "conflicts" and gh.fake.read("raw/p/p/a.md") == b"edited during the sync"


def test_an_owned_file_deleted_in_the_repo_is_recreated_like_a_local_brain_does(gh):
    gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    original = gh.fake.read("raw/p/p/a.md")
    gh.fake.commit_as_other({"raw/p/p/a.md": None}, "someone deleted it")
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 1
    assert gh.fake.read("raw/p/p/a.md") is not None and gh.fake.read("raw/p/p/a.md").split(b"synced_at")[0] == original.split(b"synced_at")[0]


def test_a_repo_copy_that_claims_our_provenance_but_differs_gets_the_same_explanation_as_locally(gh):
    src = gh.pipeline("p", {"a.md": "A"})
    sync_pipeline(gh.brain, "p", trigger="cli")
    claimed = gh.fake.read("raw/p/p/a.md") + b"\nedited\n"
    gh.fake.commit_as_other({"raw/p/p/a.md": claimed, STATE_PATH: None}, "state lost, copy edited")
    res = sync_pipeline(gh.brain, "p", trigger="cli")
    assert res.result == "conflicts" and "provenance says it was copied from this source" in res.conflicts[0]["reason"]
