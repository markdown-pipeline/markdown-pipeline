"""MD-15: mirror deletions and renames per policy; a missing source folder never causes deletions."""
import os
import shutil

import pytest

from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
import winsupport


def exists(brain, rel):
    return (brain.root / rel).exists()


def test_policy_delete_removes_owned_file_whose_source_is_gone(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    (src / "b.md").unlink()
    res = sync_pipeline(brain, "p")
    assert res.counts()["deleted"] == 1 and res.result == "ok"
    assert exists(brain, "raw/p/docs/a.md") and not exists(brain, "raw/p/docs/b.md")
    assert "raw/p/docs/b.md" not in State.load(brain).pipeline("p").files


def test_policy_keep_keeps_and_reports_source_deleted_every_time(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)], on_source_delete="keep"))
    sync_pipeline(brain, "p")
    (src / "b.md").unlink()
    for _ in range(2):
        res = sync_pipeline(brain, "p")
        assert res.counts()["kept"] == 1 and res.counts()["deleted"] == 0
        kept = [i for i in res.plan.items() if i.action == "keep"][0]
        assert "source deleted" in kept.reason
    assert exists(brain, "raw/p/docs/b.md")


def test_rename_in_source_is_delete_plus_create(brain, add_pipeline, make_source):
    src = make_source("docs", {"old.md": "same content"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    os.rename(src / "old.md", src / "new.md")
    res = sync_pipeline(brain, "p")
    c = res.counts()
    assert (c["created"], c["deleted"]) == (1, 1)
    assert exists(brain, "raw/p/docs/new.md") and not exists(brain, "raw/p/docs/old.md")


def test_rename_with_policy_keep_creates_new_and_keeps_old(brain, add_pipeline, make_source):
    src = make_source("docs", {"old.md": "x"})
    add_pipeline(Pipeline.new("p", [str(src)], on_source_delete="keep"))
    sync_pipeline(brain, "p")
    os.rename(src / "old.md", src / "new.md")
    sync_pipeline(brain, "p")
    assert exists(brain, "raw/p/docs/new.md") and exists(brain, "raw/p/docs/old.md")


def test_only_files_owned_by_this_pipeline_are_deleted(brain, add_pipeline, make_source):
    a = make_source("a", {"one.md": "1"})
    b = make_source("b", {"two.md": "2"})
    add_pipeline(Pipeline.new("pa", [str(a)]))
    add_pipeline(Pipeline.new("pb", [str(b)]))
    sync_pipeline(brain, "pa")
    sync_pipeline(brain, "pb")
    hand = brain.root / "raw/pa/a/handwritten.md"
    hand.write_text("my note")
    (brain.root / "raw/notes.md").write_text("mine")
    (a / "one.md").unlink()
    sync_pipeline(brain, "pa")
    assert not exists(brain, "raw/pa/a/one.md")
    assert hand.read_text() == "my note" and exists(brain, "raw/notes.md") and exists(brain, "raw/pb/b/two.md")


def test_a_file_edited_in_the_brain_is_not_deleted_when_its_source_disappears(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    target = brain.root / "raw/p/docs/a.md"
    target.write_text(target.read_text() + "\nmy precious addition\n")
    (src / "a.md").unlink()
    res = sync_pipeline(brain, "p", force=True)
    assert target.exists() and "precious" in target.read_text()
    assert res.counts()["conflicts"] == 1 and res.conflicts[0]["type"] == "b"


def test_missing_source_folder_deletes_nothing_and_reports_error_per_folder(brain, add_pipeline, make_source, tmp_path):
    a = make_source("a", {"one.md": "1"})
    b = make_source("b", {"two.md": "2"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    shutil.move(str(a), str(tmp_path / "renamed-a"))
    (b / "three.md").write_text("3")
    res = sync_pipeline(brain, "p")
    assert res.result == "error"
    assert exists(brain, "raw/p/A/one.md")
    assert exists(brain, "raw/p/B/three.md") and res.counts()["created"] == 1
    assert [e["source"] for e in res.errors] == ["A"] and "not found" in res.errors[0]["error"]
    kept = [i for i in res.plan.items() if i.action == "keep"]
    assert kept and "nothing deleted" in kept[0].reason


def test_missing_folder_with_policy_delete_then_restored_syncs_normally(brain, add_pipeline, make_source, tmp_path):
    a = make_source("a", {"one.md": "1"})
    add_pipeline(Pipeline.new("p", [str(a)]))
    sync_pipeline(brain, "p")
    moved = tmp_path / "moved"
    shutil.move(str(a), str(moved))
    assert sync_pipeline(brain, "p").result == "error"
    shutil.move(str(moved), str(a))
    res = sync_pipeline(brain, "p")
    assert res.result == "ok" and res.counts()["unchanged"] == 1


@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_unreadable_subfolder_deletes_nothing(brain, add_pipeline, make_source):
    if winsupport.is_root():
        pytest.skip("root ignores permissions")
    a = make_source("a", {"top.md": "T", "sub/deep.md": "D"})
    add_pipeline(Pipeline.new("p", [str(a)]))
    sync_pipeline(brain, "p")
    os.chmod(a / "sub", 0)
    try:
        res = sync_pipeline(brain, "p")
    finally:
        os.chmod(a / "sub", 0o755)
    assert res.result == "error" and res.counts()["deleted"] == 0
    assert exists(brain, "raw/p/a/sub/deep.md")


def test_missing_folders_kept_files_still_count_in_the_guard_total(brain, add_pipeline, make_source, tmp_path):
    a = make_source("a", {f"{i}.md": "x" for i in range(5)})
    b = make_source("b", {"b.md": "x"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    shutil.move(str(a), str(tmp_path / "gone"))
    res = sync_pipeline(brain, "p")
    assert res.plan.guard.total == 6


def test_label_not_in_the_pipeline_any_more_never_deletes_its_files(brain, add_pipeline, make_source):
    from mdpipeline.config import load_config, save_config

    a = make_source("a", {"one.md": "1"})
    b = make_source("b", {"two.md": "2"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p")
    cfg = load_config(brain)
    cfg.pipelines[0].sources = [s for s in cfg.pipelines[0].sources if s.label != "A"]  # hand-edited config
    save_config(brain, cfg)
    res = sync_pipeline(brain, "p")
    assert exists(brain, "raw/p/A/one.md") and res.counts()["deleted"] == 0 and res.counts()["kept"] == 1


def test_empty_brain_folders_are_pruned_after_deletes(brain, add_pipeline, make_source):
    src = make_source("docs", {"sub/a.md": "A", "keep.md": "K"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    (src / "sub/a.md").unlink()
    sync_pipeline(brain, "p")
    assert not exists(brain, "raw/p/docs/sub") and exists(brain, "raw/p/docs/keep.md")


def test_file_that_becomes_excluded_is_removed_like_a_deleted_source(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A", "drafts/b.md": "B"})
    p = add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    from mdpipeline.config import load_config, save_config

    cfg = load_config(brain)
    cfg.pipelines[0].exclude = cfg.pipelines[0].exclude + ["drafts/**"]
    save_config(brain, cfg)
    res = sync_pipeline(brain, "p")
    assert res.counts()["deleted"] == 1 and not exists(brain, "raw/p/docs/drafts/b.md")
