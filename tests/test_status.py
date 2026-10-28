"""Status computation (MD-21 AC4, MD-23 AC1): last run, result, counts, conflicts, guard state, brain age."""
import time
from datetime import datetime, timezone

from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.status import brain_status, parse_ts


def status_of(brain, name="p", now=None):
    return next(s for s in brain_status(brain, now=now)["pipelines"] if s["name"] == name)


def test_never_synced_pipeline(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    s = status_of(brain)
    assert s["status"] == "never" and s["last_run"] is None and s["age_seconds"] is None


def test_ok_run_gives_ok_status_counts_guard_and_age(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p")
    s = status_of(brain)
    assert s["status"] == "ok" and s["last_run"]["result"] == "ok"
    assert s["last_run"]["counts"]["created"] == 1
    assert s["guard"]["blocked"] is False and s["conflicts"] == []
    assert 0 <= s["age_seconds"] < 5
    assert s["owned_files"] == 1


def test_age_is_measured_from_the_last_successful_sync_not_a_later_error(brain, add_pipeline, make_source, tmp_path):
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    ok_ts = status_of(brain)["last_success"]
    src.rename(tmp_path / "gone")
    sync_pipeline(brain, "p")
    s = status_of(brain)
    assert s["status"] == "error" and s["last_success"] == ok_ts


def test_conflict_status_lists_conflicts(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    (brain.root / "raw/p/a").mkdir(parents=True)
    (brain.root / "raw/p/a/1.md").write_text("hand")
    sync_pipeline(brain, "p")
    s = status_of(brain)
    assert s["status"] == "conflicts" and s["conflicts"] == [{"type": "a", "path": "raw/p/a/1.md"}]


def test_paused_pipeline_status(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    cfg = load_config(brain)
    cfg.pipelines[0].paused = True
    save_config(brain, cfg)
    assert status_of(brain)["status"] == "paused"


def test_brain_age_is_the_age_of_the_most_recent_successful_sync(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    assert brain_status(brain)["brain_age_seconds"] is None
    sync_pipeline(brain, "p")
    later = time.time() + 3600
    assert 3599 < brain_status(brain, now=later)["brain_age_seconds"] < 3605


def test_parse_ts_round_trip():
    assert parse_ts("2026-10-02T10:00:00.500000Z") == datetime(2026, 10, 2, 10, 0, 0, 500000, tzinfo=timezone.utc).timestamp()
