"""MD-122 (FR-S8): what changed in the last sync, and in a time window, read from the local activity log."""
import pytest

from conftest import write
from mdpipeline import changes
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog


@pytest.fixture
def world(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "# A\n", "b.md": "# B\n", "c.md": "# C\n"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    return brain, src


def test_a_pipeline_never_synced_has_no_last_sync(world):
    brain, _ = world
    assert changes.last_sync(brain, "p") is None


def test_first_sync_counts_everything_as_new(world):
    brain, _ = world
    sync_pipeline(brain, "p")
    last = changes.last_sync(brain, "p")
    assert last["counts"] == {"new": 3, "changed": 0, "deleted": 0}
    assert last["text"] == "3 new"


def test_text_uses_new_changed_deleted_in_that_order_and_skips_zeros(world):
    brain, src = world
    sync_pipeline(brain, "p")
    write(src / "a.md", "# A edited\n")
    write(src / "d.md", "# D\n")
    (src / "c.md").unlink()
    sync_pipeline(brain, "p")
    last = changes.last_sync(brain, "p")
    assert last["counts"] == {"new": 1, "changed": 1, "deleted": 1}
    assert last["text"] == "1 new · 1 changed · 1 deleted"


def test_a_sync_with_nothing_to_do_says_no_changes_and_is_still_the_last_sync(world):
    brain, _ = world
    sync_pipeline(brain, "p")
    sync_pipeline(brain, "p")
    last = changes.last_sync(brain, "p")
    assert last["text"] == "no changes" and last["items"] == []


def test_items_list_the_operation_path_and_a_title_from_the_file_name(world):
    brain, src = world
    sync_pipeline(brain, "p")
    write(src / "a.md", "# A edited\n")
    sync_pipeline(brain, "p")
    [item] = changes.last_sync(brain, "p")["items"]
    assert item["op"] == "changed" and item["path"].endswith("a.md") and item["title"] == "a"


def test_dry_runs_never_count_as_a_sync(world):
    brain, src = world
    sync_pipeline(brain, "p")
    write(src / "z.md", "# Z\n")
    sync_pipeline(brain, "p", dry_run=True)
    assert changes.last_sync(brain, "p")["text"] == "3 new"


def test_a_paused_or_busy_run_is_not_a_sync(world):
    brain, _ = world
    sync_pipeline(brain, "p")
    ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "ui", "result": "busy", "counts": {}, "duration_ms": 0})
    assert changes.last_sync(brain, "p")["text"] == "3 new"


def test_a_connector_source_is_listed_by_key_never_by_title(brain):
    ActivityLog(brain).append({"type": "run", "pipeline": "j", "trigger": "ui", "result": "ok", "duration_ms": 1,
                               "counts": {"created": 1, "updated": 0, "deleted": 0},
                               "files": [{"source_label": "ACME", "source_path": "", "action": "create", "key": "ACME-7"}]})
    [item] = changes.last_sync(brain, "j")["items"]
    assert item["title"] == "ACME-7" and item["op"] == "new"


def test_renamed_files_count_as_changed(brain):
    ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "ui", "result": "ok", "duration_ms": 1,
                               "counts": {"renamed": 1}, "files": [{"action": "rename", "target": "raw/p/x/new name.md", "from": "raw/p/x/old.md"}]})
    last = changes.last_sync(brain, "p")
    assert last["counts"]["changed"] == 1 and last["items"][0]["op"] == "changed"


# --- recent(): the MCP read -------------------------------------------------------------------------------------
def _run(brain, ts, pipeline, files):
    ActivityLog(brain).append({"type": "run", "ts": ts, "pipeline": pipeline, "trigger": "ui", "result": "ok", "duration_ms": 1, "counts": {}, "files": files})


def _f(action, target):
    return {"action": action, "target": target}


def test_recent_covers_only_runs_since_the_given_time(brain):
    _run(brain, "2026-10-10T08:00:00.000000Z", "p", [_f("create", "raw/p/old.md")])
    _run(brain, "2026-10-12T08:00:00.000000Z", "p", [_f("create", "raw/p/new.md")])
    out = changes.recent(brain, since="2026-10-11T00:00:00Z")
    assert [i["path"] for i in out["items"]] == ["raw/p/new.md"]
    assert out["counts"] == {"new": 1, "changed": 0, "deleted": 0} and out["syncs"] == 1


def test_recent_merges_the_same_file_across_syncs(brain):
    _run(brain, "2026-10-12T08:00:00.000000Z", "p", [_f("create", "raw/p/a.md"), _f("create", "raw/p/gone.md")])
    _run(brain, "2026-10-12T09:00:00.000000Z", "p", [_f("update", "raw/p/a.md"), _f("delete", "raw/p/gone.md")])
    out = changes.recent(brain, since="2026-10-12T00:00:00Z")
    assert {i["path"]: i["op"] for i in out["items"]} == {"raw/p/a.md": "new"}      # new then edited stays new; new then deleted vanishes


def test_recent_edit_then_delete_is_deleted(brain):
    _run(brain, "2026-10-12T08:00:00.000000Z", "p", [_f("update", "raw/p/a.md")])
    _run(brain, "2026-10-12T09:00:00.000000Z", "p", [_f("delete", "raw/p/a.md")])
    assert {i["path"]: i["op"] for i in changes.recent(brain, since="2026-10-12T00:00:00Z")["items"]} == {"raw/p/a.md": "deleted"}


def test_recent_can_be_limited_to_one_pipeline(brain):
    _run(brain, "2026-10-12T08:00:00.000000Z", "p", [_f("create", "raw/p/a.md")])
    _run(brain, "2026-10-12T08:00:00.000000Z", "q", [_f("create", "raw/q/b.md")])
    assert [i["pipeline"] for i in changes.recent(brain, since="2026-10-12T00:00:00Z", pipeline="q")["items"]] == ["q"]


def test_recent_without_since_defaults_to_the_last_24_hours(brain):
    _run(brain, "2000-01-01T00:00:00.000000Z", "p", [_f("create", "raw/p/ancient.md")])
    assert changes.recent(brain)["items"] == []


def test_recent_rejects_a_since_that_is_not_a_time(brain):
    with pytest.raises(ValueError):
        changes.recent(brain, since="yesterday-ish")


def test_recent_is_capped_and_says_so(brain):
    _run(brain, "2026-10-12T08:00:00.000000Z", "p", [_f("create", f"raw/p/f{i}.md") for i in range(5)])
    out = changes.recent(brain, since="2026-10-12T00:00:00Z", limit=2)
    assert len(out["items"]) == 2 and out["truncated"] is True and out["counts"]["new"] == 5
