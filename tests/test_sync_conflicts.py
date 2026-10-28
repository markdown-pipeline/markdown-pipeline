"""MD-16: ownership and conflicts; hand-written or brain-edited files are never lost."""
import pytest

from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.state import State


def setup(brain, add_pipeline, make_source, files=None, **kw):
    src = make_source("docs", files or {"a.md": "SOURCE A"})
    add_pipeline(Pipeline.new("p", [str(src)], **kw))
    return src


def test_conflict_a_hand_written_note_at_the_target_is_skipped_and_reported(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    hand = brain.root / "raw/p/docs/a.md"
    hand.parent.mkdir(parents=True)
    hand.write_text("my hand written note")
    res = sync_pipeline(brain, "p")
    assert hand.read_text() == "my hand written note"
    assert res.result == "conflicts" and res.counts()["conflicts"] == 1
    assert res.conflicts[0]["type"] == "a" and res.conflicts[0]["path"] == "raw/p/docs/a.md"
    assert "raw/p/docs/a.md" not in State.load(brain).pipeline("p").files


def test_conflict_a_persists_and_is_never_overwritten_even_with_force(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    hand = brain.root / "raw/p/docs/a.md"
    hand.parent.mkdir(parents=True)
    hand.write_text("mine")
    for force in (False, True, True):
        res = sync_pipeline(brain, "p", force=force)
        assert res.conflicts[0]["type"] == "a"
    assert hand.read_text() == "mine"


def test_a_file_that_merely_looks_owned_by_its_provenance_is_not_owned_without_state(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    target = brain.root / "raw/p/docs/a.md"
    copy = target.read_text()
    (brain.config_dir / "state.json").unlink()      # ownership state lost
    (brain.root / "raw/p/docs/a.md").write_text(copy)  # provenance says pipeline p, but no state
    res = sync_pipeline(brain, "p", force=True)
    assert res.conflicts and res.conflicts[0]["type"] == "a"
    assert target.read_text() == copy


def test_conflict_b_edited_owned_file_is_skipped_by_default(brain, add_pipeline, make_source):
    src = setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    target = brain.root / "raw/p/docs/a.md"
    target.write_text(target.read_text() + "\nMY EDIT\n")
    (src / "a.md").write_text("SOURCE A v2")
    res = sync_pipeline(brain, "p")
    assert "MY EDIT" in target.read_text() and "v2" not in target.read_text()
    assert res.conflicts[0]["type"] == "b" and res.result == "conflicts"


def test_conflict_b_is_reported_even_if_the_source_did_not_change(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    target = brain.root / "raw/p/docs/a.md"
    target.write_text(target.read_text() + "x")
    assert sync_pipeline(brain, "p").conflicts[0]["type"] == "b"


def test_editing_only_the_provenance_block_in_the_brain_is_still_a_brain_edit(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    target = brain.root / "raw/p/docs/a.md"
    target.write_text(target.read_text().replace("pipeline: p", "pipeline: q"))
    assert sync_pipeline(brain, "p").conflicts[0]["type"] == "b"


def test_reverting_the_edit_clears_the_conflict(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    target = brain.root / "raw/p/docs/a.md"
    original = target.read_bytes()
    target.write_bytes(original + b"x")
    assert sync_pipeline(brain, "p").result == "conflicts"
    target.write_bytes(original)
    assert sync_pipeline(brain, "p").result == "ok"


def test_force_overwrites_conflict_b_only(brain, add_pipeline, make_source):
    src = setup(brain, add_pipeline, make_source, {"a.md": "SOURCE A", "b.md": "SOURCE B"})
    sync_pipeline(brain, "p")
    a = brain.root / "raw/p/docs/a.md"
    a.write_text("edited A")
    (brain.root / "raw/p/docs/new.md").write_text("hand")
    (src / "new.md").write_text("collides with the hand written one")
    res = sync_pipeline(brain, "p", force=True)
    assert "SOURCE A" in a.read_text() and "edited A" not in a.read_text()
    assert (brain.root / "raw/p/docs/new.md").read_text() == "hand"
    assert [c["type"] for c in res.conflicts] == ["a"]


def test_after_a_forced_overwrite_the_file_is_clean_again(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    (brain.root / "raw/p/docs/a.md").write_text("edit")
    sync_pipeline(brain, "p", force=True)
    assert sync_pipeline(brain, "p").counts()["unchanged"] == 1


def test_conflicts_are_logged_with_type_and_path(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    (brain.root / "raw/p/docs").mkdir(parents=True)
    (brain.root / "raw/p/docs/a.md").write_text("hand")
    sync_pipeline(brain, "p")
    run = ActivityLog(brain).read(types={"run"})[-1]
    assert run["conflicts"] == [{"type": "a", "path": "raw/p/docs/a.md"}]
    assert run["result"] == "conflicts"


def test_force_use_is_logged_as_an_event(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    (brain.root / "raw/p/docs/a.md").write_text("edit")
    sync_pipeline(brain, "p", trigger="cli", force=True)
    ev = [e for e in ActivityLog(brain).read(types={"event"}) if e["action"] == "force"]
    assert len(ev) == 1 and ev[0]["trigger"] == "cli" and ev[0]["paths"] == ["raw/p/docs/a.md"]


def test_files_of_a_removed_pipeline_are_not_re_owned_by_a_new_pipeline_with_the_same_name(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    sync_pipeline(brain, "p")
    st = State.load(brain)
    st.drop_pipeline("p")      # what removing the pipeline does (MD-10)
    st.save()
    target = brain.root / "raw/p/docs/a.md"
    before = target.read_bytes()
    res = sync_pipeline(brain, "p", force=True)
    assert res.conflicts[0]["type"] == "a" and target.read_bytes() == before


def test_another_pipelines_file_is_a_conflict_a_not_an_overwrite(brain, add_pipeline, make_source):
    s1 = make_source("one", {"x.md": "from one"})
    s2 = make_source("two", {"x.md": "from two"})
    add_pipeline(Pipeline.new("p1", [str(s1)], target="raw/shared/{relpath}"))
    add_pipeline(Pipeline.new("p2", [str(s2)], target="raw/shared/{relpath}"))
    sync_pipeline(brain, "p1")
    res = sync_pipeline(brain, "p2", force=True)
    assert res.conflicts[0]["type"] == "a"
    assert "from one" in (brain.root / "raw/shared/x.md").read_text()


def test_force_paths_overwrites_only_the_named_conflict_b_files(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source, {"a.md": "SOURCE A", "b.md": "SOURCE B"})
    sync_pipeline(brain, "p")
    for name in ("a", "b"):
        (brain.root / f"raw/p/docs/{name}.md").write_text(f"edited {name}")
    res = sync_pipeline(brain, "p", force_paths=frozenset({"raw/p/docs/a.md"}), trigger="ui")
    assert "SOURCE A" in (brain.root / "raw/p/docs/a.md").read_text()
    assert (brain.root / "raw/p/docs/b.md").read_text() == "edited b"
    assert [c["path"] for c in res.conflicts] == ["raw/p/docs/b.md"]
    ev = [e for e in ActivityLog(brain).read(types={"event"}) if e["action"] == "force"]
    assert ev[0]["trigger"] == "ui" and ev[0]["paths"] == ["raw/p/docs/a.md"]


def test_force_paths_never_overwrites_conflict_a(brain, add_pipeline, make_source):
    setup(brain, add_pipeline, make_source)
    hand = brain.root / "raw/p/docs/a.md"
    hand.parent.mkdir(parents=True)
    hand.write_text("mine")
    sync_pipeline(brain, "p", force_paths=frozenset({"raw/p/docs/a.md"}))
    assert hand.read_text() == "mine"
