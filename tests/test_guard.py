"""MD-18: the big-change guard stops a sync that would delete or overwrite too much, and asks for approval."""
import pytest

from conftest import tree_snapshot
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import cancel_pending, sync_all, sync_pipeline
from mdpipeline.guard import evaluate_guard
from mdpipeline.log import ActivityLog
from mdpipeline.state import State
from mdpipeline.status import brain_status


def make(brain, add_pipeline, make_source, n=10, **kw):
    src = make_source("docs", {f"n{i}.md": f"note {i}" for i in range(n)})
    add_pipeline(Pipeline.new("p", [str(src)], **kw))
    sync_pipeline(brain, "p")
    return src


def delete_files(src, count):
    for i in range(count):
        (src / f"n{i}.md").unlink()


# formula (A2, PM-confirmed) ---------------------------------------------------------------
@pytest.mark.parametrize("count,total,expected", [
    (5, 10, True),      # 50% > 40% and >= 5 files
    (4, 10, False),     # exactly 40% is not above the threshold
    (3, 4, False),      # 75% but fewer than 5 files
    (5, 5, True),
    (0, 0, False),
    (10, 10, True),
])
def test_guard_formula(count, total, expected):
    assert evaluate_guard(count, total, 40, 5).blocked is expected


def test_both_conditions_must_hold_threshold_and_minimum():
    assert evaluate_guard(5, 100, 40, 5).blocked is False   # 5% only
    assert evaluate_guard(6, 10, 40, 10).blocked is False   # below min files


def test_blocked_sync_writes_nothing_and_reports_count_percentage_and_file_list(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    raw_before = tree_snapshot(brain_dir / "raw")
    state_files_before = dict(State.load(brain).pipeline("p").files)
    res = sync_pipeline(brain, "p")
    assert res.result == "blocked"
    assert tree_snapshot(brain_dir / "raw") == raw_before
    assert State.load(brain).pipeline("p").files == state_files_before
    d = res.to_dict()
    assert d["guard"]["count"] == 6 and d["guard"]["total"] == 10 and d["guard"]["percent"] == 60.0
    assert d["guard"]["threshold"] == 40 and d["guard"]["min_files"] == 5
    assert sorted(f["target"] for f in d["affected_files"]) == [f"raw/p/docs/n{i}.md" for i in range(6)]
    assert "60%" in res.message and "approve" in res.message.lower()


def test_normal_updates_count_toward_the_guard(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    for i in range(10):
        (src / f"n{i}.md").write_text(f"changed {i}")
    assert sync_pipeline(brain, "p").result == "blocked"


def test_forced_overwrites_count_toward_the_guard(brain, brain_dir, add_pipeline, make_source):
    make(brain, add_pipeline, make_source)
    for i in range(6):
        (brain_dir / f"raw/p/docs/n{i}.md").write_text("edited")
    assert sync_pipeline(brain, "p", force=True).result == "blocked"


def test_new_files_do_not_count(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    for i in range(30):
        (src / f"new{i}.md").write_text("new")
    assert sync_pipeline(brain, "p").result == "ok"


def test_threshold_and_minimum_are_configurable_per_pipeline(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source, guard_threshold_percent=90, guard_min_files=1)
    delete_files(src, 6)
    assert sync_pipeline(brain, "p").result == "ok"


def test_dry_run_reports_blocked_and_writes_nothing(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    before = tree_snapshot(brain_dir)
    res = sync_pipeline(brain, "p", dry_run=True)
    assert res.result == "blocked" and res.plan.guard.blocked
    assert tree_snapshot(brain_dir) == before


# approval ---------------------------------------------------------------------------------
def test_approval_applies_exactly_the_change_set_that_was_shown(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    assert sync_pipeline(brain, "p").result == "blocked"
    res = sync_pipeline(brain, "p", approve=True)
    assert res.result == "ok" and res.counts()["deleted"] == 6
    assert sorted(p.name for p in (brain_dir / "raw/p/docs").glob("*.md")) == [f"n{i}.md" for i in range(6, 10)]
    status = next(s for s in brain_status(brain)["pipelines"] if s["name"] == "p")
    assert status["status"] == "ok"


def test_approve_without_having_seen_the_change_set_applies_nothing(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    before = tree_snapshot(brain_dir / "raw")
    res = sync_pipeline(brain, "p", approve=True)
    assert res.result == "blocked" and tree_snapshot(brain_dir / "raw") == before
    assert sync_pipeline(brain, "p", approve=True).result == "ok"    # now it was shown


def test_if_sources_changed_since_the_change_set_was_shown_it_asks_again(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    sync_pipeline(brain, "p")                       # shown: delete n0..n5
    (src / "n6.md").unlink()                        # the sources changed since
    before = tree_snapshot(brain_dir / "raw")
    res = sync_pipeline(brain, "p", approve=True)
    assert res.result == "blocked" and tree_snapshot(brain_dir / "raw") == before
    assert "changed since" in res.message
    assert res.to_dict()["guard"]["count"] == 7
    assert sync_pipeline(brain, "p", approve=True).result == "ok"


def test_a_modified_source_content_also_invalidates_the_approval(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    for i in range(10):
        (src / f"n{i}.md").write_text(f"v2 {i}")
    sync_pipeline(brain, "p")
    (src / "n3.md").write_text("v3 sneaky")
    assert sync_pipeline(brain, "p", approve=True).result == "blocked"


def test_the_mcp_trigger_can_never_approve(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    sync_pipeline(brain, "p")
    before = tree_snapshot(brain_dir / "raw")
    res = sync_pipeline(brain, "p", trigger="mcp", approve=True)
    assert res.result == "blocked" and tree_snapshot(brain_dir / "raw") == before


def test_ui_trigger_can_approve(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    sync_pipeline(brain, "p", trigger="ui")
    assert sync_pipeline(brain, "p", trigger="ui", approve=True).result == "ok"


# cancel -----------------------------------------------------------------------------------
def test_cancel_records_the_run_as_cancelled_and_the_pipeline_stays_blocked(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    sync_pipeline(brain, "p")
    res = cancel_pending(brain, "p", trigger="ui")
    assert res.result == "cancelled"
    runs = ActivityLog(brain).read(types={"run"})
    assert runs[-1]["result"] == "cancelled" and runs[-1]["trigger"] == "ui"
    s = next(x for x in brain_status(brain)["pipelines"] if x["name"] == "p")
    assert s["status"] == "blocked"


def test_approve_after_cancel_does_not_apply_the_cancelled_change_set(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    sync_pipeline(brain, "p")
    cancel_pending(brain, "p", trigger="cli")
    before = tree_snapshot(brain_dir / "raw")
    assert sync_pipeline(brain, "p", approve=True).result == "blocked"
    assert tree_snapshot(brain_dir / "raw") == before


def test_next_sync_after_cancel_rechecks_and_clears_blocked_if_the_cause_is_gone(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    for i in range(6):
        (src / f"n{i}.md").rename(src / f"hold{i}.txt")
    sync_pipeline(brain, "p")
    cancel_pending(brain, "p", trigger="ui")
    for i in range(6):
        (src / f"hold{i}.txt").rename(src / f"n{i}.md")
    assert sync_pipeline(brain, "p").result == "ok"
    assert next(x for x in brain_status(brain)["pipelines"])["status"] == "ok"


def test_cancel_with_nothing_pending_is_an_error(brain, add_pipeline, make_source):
    make(brain, add_pipeline, make_source)
    with pytest.raises(ValueError, match="nothing to cancel"):
        cancel_pending(brain, "p", trigger="cli")


# logging ----------------------------------------------------------------------------------
def test_blocked_approved_and_cancelled_events_are_logged(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    sync_pipeline(brain, "p", trigger="cli")
    cancel_pending(brain, "p", trigger="ui")
    sync_pipeline(brain, "p", trigger="cli")
    sync_pipeline(brain, "p", trigger="cli", approve=True)
    log = ActivityLog(brain).read()
    results = [e["result"] for e in log if e["type"] == "run"]
    assert results[1:] == ["blocked", "cancelled", "blocked", "ok"]
    events = [(e["action"], e["trigger"]) for e in log if e["type"] == "event"]
    assert ("cancel", "ui") in events and ("approve", "cli") in events
    blocked = [e for e in log if e["type"] == "run" and e["result"] == "blocked"][0]
    assert blocked["guard"]["blocked"] is True and blocked["guard"]["count"] == 6


# sync all ---------------------------------------------------------------------------------
def test_one_blocked_pipeline_does_not_stop_the_others(brain, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    other = make_source("other", {"o.md": "o"})
    add_pipeline(Pipeline.new("q", [str(other)]))
    delete_files(src, 6)
    results = {r.pipeline: r.result for r in sync_all(brain)}
    assert results == {"p": "blocked", "q": "ok"}


# CLI --------------------------------------------------------------------------------------
def test_cli_blocked_exit_3_then_approve_exit_0(brain, add_pipeline, make_source, capsys):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    code = main(["--brain", str(brain.root), "sync", "p"])
    out = capsys.readouterr().out
    assert code == 3 and "--approve" in out and "6 of 10" in out and "raw/p/docs/n0.md" in out
    code = main(["--brain", str(brain.root), "sync", "p", "--approve"])
    assert code == 0


def test_cli_cancel(brain, add_pipeline, make_source, capsys):
    src = make(brain, add_pipeline, make_source)
    delete_files(src, 6)
    main(["--brain", str(brain.root), "sync", "p"])
    assert main(["--brain", str(brain.root), "cancel", "p"]) == 0
    assert "cancelled" in capsys.readouterr().out
    assert main(["--brain", str(brain.root), "cancel", "p"]) == 2   # nothing pending any more


def test_approval_of_an_update_heavy_change_set_works_even_though_timestamps_differ(brain, brain_dir, add_pipeline, make_source):
    src = make(brain, add_pipeline, make_source)
    for i in range(10):
        (src / f"n{i}.md").write_text(f"v2 {i}")
    assert sync_pipeline(brain, "p", now=1_800_000_000.0).result == "blocked"
    res = sync_pipeline(brain, "p", approve=True, now=1_800_000_500.0)   # different synced_at, same change set
    assert res.result == "ok" and res.counts()["updated"] == 10
    assert "v2 3" in (brain_dir / "raw/p/docs/n3.md").read_text()
