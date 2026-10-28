"""MD-45: the engine plans, guards, writes, owns and recovers through one source contract (a fake connector proves it)."""
import json
from pathlib import Path

import pytest

from conftest import write
from fake_source import FakeWorld, iso
from mdpipeline.config import Config, Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.sources import LocalFolderSource, adapter_for
from mdpipeline.state import State


@pytest.fixture
def world():
    return FakeWorld("w")


@pytest.fixture
def pipe(brain, add_pipeline, world):
    world.put("i1", "PRJ/one.md", "# One\nbody\n")
    world.put("i2", "PRJ/two.md", "# Two\n")
    add_pipeline(Pipeline("p", [world.source("fake")], guard_threshold_percent=100))
    return brain


def read(brain, rel):
    return (brain.root / rel).read_text()


def test_the_folder_source_runs_through_the_contract(make_source):
    src = make_source("docs", {"a.md": "A"})
    adapter = adapter_for(Source(str(src)))
    assert isinstance(adapter, LocalFolderSource)
    entry = adapter.scan(["**/*.md"], []).entries[0]
    assert entry.identity == entry.relpath == "a.md" and entry.created is None and entry.version == ""


def test_a_source_of_another_type_round_trips_through_the_config(brain, add_pipeline, world):
    add_pipeline(Pipeline("p", [world.source("fake"), Source("/tmp/x", "docs")]))
    cfg = load_config(brain)
    assert [s.type for s in cfg.get("p").sources] == ["fake", "folder"]
    assert cfg.get("p").sources[0].options == {"world": "w"}


def test_folder_sources_are_saved_exactly_as_before(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", ["/tmp/x"]))
    sources_text = brain.config_path.read_text().split("sources:")[1].split("target:")[0]
    assert "type:" not in sources_text and "options" not in sources_text


def test_first_sync_creates_files_with_provenance_naming_the_connector(pipe):
    res = sync_pipeline(pipe, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 2
    meta, body = parse_document(read(pipe, "raw/p/fake/PRJ/one.md"))
    prov = meta["provenance"]
    assert body == "# One\nbody\n" and prov["source_path"] == "i1" and prov["fake_id"] == "i1" and prov["source_label"] == "fake"
    assert prov["source"] == "fake"


def test_second_sync_with_no_change_performs_zero_writes_and_reads_no_content(pipe, world):
    sync_pipeline(pipe, "p", trigger="cli")
    world.content_reads.clear()
    before = {p: p.stat().st_mtime_ns for p in (pipe.root / "raw").rglob("*.md")}
    res = sync_pipeline(pipe, "p", trigger="cli")
    assert res.counts()["unchanged"] == 2 and res.counts()["created"] == res.counts()["updated"] == 0
    assert world.content_reads == []                                   # the recorded version matched: nothing was fetched
    assert before == {p: p.stat().st_mtime_ns for p in (pipe.root / "raw").rglob("*.md")}


def test_a_changed_version_fetches_and_updates_only_that_item(pipe, world):
    sync_pipeline(pipe, "p", trigger="cli")
    world.content_reads.clear()
    world.put("i1", "PRJ/one.md", "# One changed\n")
    res = sync_pipeline(pipe, "p", trigger="cli")
    assert res.counts()["updated"] == 1 and world.content_reads == ["i1"]
    assert "changed" in read(pipe, "raw/p/fake/PRJ/one.md")


def test_an_item_removed_at_the_source_follows_the_on_delete_policy(pipe, world):
    sync_pipeline(pipe, "p", trigger="cli")
    world.remove("i2")
    res = sync_pipeline(pipe, "p", trigger="cli")
    assert res.counts()["deleted"] == 1 and not (pipe.root / "raw/p/fake/PRJ/two.md").exists()


def test_on_delete_keep_keeps_the_file(brain, add_pipeline, world):
    world.put("i1", "one.md", "x")
    add_pipeline(Pipeline("p", [world.source()], on_source_delete="keep", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    world.remove("i1")
    assert sync_pipeline(brain, "p", trigger="cli").counts()["kept"] == 1 and (brain.root / "raw/p/fake/one.md").exists()


def test_a_failing_connector_never_deletes(pipe, world):
    sync_pipeline(pipe, "p", trigger="cli")
    for failure in ("service unavailable", RuntimeError("boom")):
        world.fail(failure)
        res = sync_pipeline(pipe, "p", trigger="cli")
        assert res.result == "error" and res.counts()["deleted"] == 0
        assert (pipe.root / "raw/p/fake/PRJ/one.md").exists() and (pipe.root / "raw/p/fake/PRJ/two.md").exists()
    world.fail(None)
    assert sync_pipeline(pipe, "p", trigger="cli").counts()["unchanged"] == 2


def test_a_failing_connector_does_not_stop_the_other_sources(brain, add_pipeline, world, make_source):
    folder = make_source("docs", {"a.md": "A"})
    world.put("i1", "one.md", "x")
    add_pipeline(Pipeline("p", [world.source("fake"), Source(str(folder), "docs")]))
    world.fail("down")
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and (brain.root / "raw/p/docs/a.md").exists()


def test_an_empty_answer_for_a_source_that_owns_files_always_asks_the_guard(brain, add_pipeline, world):
    for i in range(3):
        world.put(f"i{i}", f"n{i}.md", str(i))
    add_pipeline(Pipeline("p", [world.source()], guard_threshold_percent=100, guard_min_files=50))
    sync_pipeline(brain, "p", trigger="cli")
    for i in range(3):
        world.remove(f"i{i}")
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "blocked" and "returned 0" in res.plan.guard.message and (brain.root / "raw/p/fake/n0.md").exists()
    assert sync_pipeline(brain, "p", trigger="cli", approve=True).counts()["deleted"] == 3


def test_a_changed_display_path_with_the_same_identity_is_one_rename_not_delete_and_create(brain, add_pipeline, world):
    world.put("i1", "OLD/item.md", "x")
    add_pipeline(Pipeline("p", [world.source()], guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    world.put("i1", "NEW/item renamed.md", "x")
    res = sync_pipeline(brain, "p", trigger="cli")
    c = res.counts()
    assert c["created"] == 0 and c["deleted"] == 0 and c["renamed"] + c["updated"] == 1
    assert list((brain.root / "raw").rglob("*.md")) and len(list((brain.root / "raw").rglob("*.md"))) == 1


def test_date_name_policy_uses_the_items_created_date_and_names_stay_stable(brain, add_pipeline, world):
    world.put("i1", "PRJ/KEY-1 first title.md", "x", created=1_700_000_000.0)          # 2023-11-14
    add_pipeline(Pipeline("p", [world.source()], name_policy="date-name", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    [path] = list((brain.root / "raw").rglob("*.md"))
    assert path.name.startswith("2023-11-14") and "key-1-first-title" in path.name
    world.put("i1", "PRJ/KEY-1 a completely different title.md", "x2")
    sync_pipeline(brain, "p", trigger="cli")
    assert [p.name for p in (brain.root / "raw").rglob("*.md")] == [path.name]


def test_state_records_identity_and_version(pipe):
    sync_pipeline(pipe, "p", trigger="cli")
    rec = State.load(pipe).pipeline("p").files["raw/p/fake/PRJ/one.md"]
    assert rec.source_path == "i1" and rec.source_version == "v1"


def test_the_source_contract_is_documented():
    doc = (Path(__file__).parent.parent / "docs" / "source-contract.md").read_text()
    for name in ("identity", "relpath", "created", "version", "content", "known", "guard_empty", "unreadable", "never read as",
                 "register_source_type", "provenance"):
        assert name in doc


class Kill(BaseException):
    pass


def test_a_brain_edited_copy_of_a_connector_item_is_a_conflict_and_never_overwritten(pipe, world):
    sync_pipeline(pipe, "p", trigger="cli")
    path = pipe.root / "raw/p/fake/PRJ/one.md"
    path.write_text(path.read_text() + "my note\n")
    world.put("i1", "PRJ/one.md", "# One, changed at the source\n")
    res = sync_pipeline(pipe, "p", trigger="cli")
    assert res.result == "conflicts" and "my note" in path.read_text()


def test_a_hand_written_file_at_a_connector_target_is_never_claimed(brain, brain_dir, add_pipeline, world):
    write(brain_dir / "raw/p/fake/one.md", "hand written")
    world.put("i1", "one.md", "x")
    add_pipeline(Pipeline("p", [world.source()]))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "conflicts" and (brain_dir / "raw/p/fake/one.md").read_text() == "hand written"


def test_a_kill_between_files_converges_without_duplicates_or_false_conflicts(brain, add_pipeline, world):
    for i in range(3):
        world.put(f"i{i}", f"n{i}.md", str(i))
    add_pipeline(Pipeline("p", [world.source()]))
    seen = []

    def die(item):
        seen.append(item)
        if len(seen) == 2:
            raise Kill()

    with pytest.raises(Kill):
        sync_pipeline(brain, "p", trigger="cli", after_write=die)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["conflicts"] == 0 and len(list((brain.root / "raw").rglob("*.md"))) == 3
    assert sync_pipeline(brain, "p", trigger="cli").counts()["unchanged"] == 3


def test_an_item_whose_content_cannot_be_produced_fails_alone_and_is_never_deleted(brain, add_pipeline, world):
    import fake_source

    world.put("i1", "one.md", "1")
    world.put("i2", "two.md", "2")
    add_pipeline(Pipeline("p", [world.source()], guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    world.put("i1", "one.md", "1 changed")
    world.put("i2", "two.md", "2 changed")
    fake_source.BROKEN.add("i1")
    try:
        res = sync_pipeline(brain, "p", trigger="cli")
    finally:
        fake_source.BROKEN.clear()
    assert res.result == "error" and res.counts()["errors"] == 1 and res.counts()["updated"] == 1 and res.counts()["deleted"] == 0
    assert "cannot convert" in res.errors[0]["error"] and (brain.root / "raw/p/fake/one.md").exists()
