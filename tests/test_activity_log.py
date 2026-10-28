"""MD-19: every run is logged locally with the required fields; no telemetry, no network."""
import socket

import pytest

from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.locking import brain_lock
from mdpipeline.log import ActivityLog


def runs(brain):
    return ActivityLog(brain).read(types={"run"})


def test_run_record_has_time_duration_pipeline_trigger_counts_per_source_guard_errors_result(brain, add_pipeline, make_source):
    a = make_source("a", {"1.md": "1", "2.md": "2"})
    b = make_source("b", {"3.md": "3"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")]))
    sync_pipeline(brain, "p", trigger="ui")
    run = runs(brain)[-1]
    assert run["ts"].endswith("Z") and isinstance(run["duration_ms"], int)
    assert (run["pipeline"], run["trigger"], run["result"]) == ("p", "ui", "ok")
    assert run["sources"]["A"]["created"] == 2 and run["sources"]["B"]["created"] == 1
    assert run["counts"]["created"] == 3
    for key in ("created", "updated", "deleted", "kept", "skipped", "conflicts"):
        assert key in run["counts"]
    assert run["guard"]["blocked"] is False and "count" in run["guard"]
    assert run["errors"] == []


def test_file_level_changes_are_recorded_for_the_ui_to_expand(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    files = runs(brain)[-1]["files"]
    assert files == [{"source_label": "a", "source_path": "1.md", "target": "raw/p/a/1.md", "action": "create"}]


@pytest.mark.parametrize("trigger", ["cli", "mcp", "ui"])
def test_trigger_values_are_recorded(brain, add_pipeline, make_source, trigger):
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger=trigger)
    assert runs(brain)[-1]["trigger"] == trigger


def test_error_result_and_error_detail_are_logged(brain, add_pipeline, tmp_path):
    add_pipeline(Pipeline.new("p", [str(tmp_path / "missing")]))
    sync_pipeline(brain, "p")
    run = runs(brain)[-1]
    assert run["result"] == "error" and run["errors"][0]["source"] == "missing"


def test_paused_run_is_logged_as_skipped_paused(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "1"})
    p = add_pipeline(Pipeline.new("p", [str(src)]))
    cfg = load_config(brain)
    cfg.pipelines[0].paused = True
    save_config(brain, cfg)
    res = sync_pipeline(brain, "p")
    assert res.result == "skipped: paused" and runs(brain)[-1]["result"] == "skipped: paused"
    assert not (brain.raw_dir / "p").exists()


def test_overlapping_sync_writes_nothing_returns_busy_and_is_logged(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    with brain_lock(brain):
        res = sync_pipeline(brain, "p")
    assert res.result == "busy" and "another sync is running" in res.message
    assert not (brain.raw_dir / "p").exists()
    assert runs(brain)[-1]["result"] == "busy"
    assert sync_pipeline(brain, "p").result == "ok"  # lock released


def test_a_whole_sync_works_with_the_network_disabled(brain, add_pipeline, make_source, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    assert sync_pipeline(brain, "p").result == "ok"


def test_log_is_a_local_file_inside_the_brain_bookkeeping_folder(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p")
    assert (brain.config_dir / "log.jsonl").is_file()
