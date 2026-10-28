"""MD-121 AC1-3: a scheduled run is a normal run (same plan, guard, no deletion on failure) started by the clock."""
import shutil
import time

import pytest

from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.scheduler import last_run_ts, run_due

HOUR = 3600


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "one", "2.md": "two"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    return src


def runs(brain):
    return [r for r in ActivityLog(brain).read(types={"run"}) if r["pipeline"] == "p"]


def test_nothing_runs_before_the_interval_has_passed(brain, synced):
    assert run_due(brain, now=time.time() + HOUR - 120) == []
    assert len(runs(brain)) == 1


def test_a_due_pipeline_is_synced_with_the_schedule_trigger(brain, synced):
    results = run_due(brain, now=time.time() + HOUR + 5)
    assert [(r.pipeline, r.trigger, r.result) for r in results] == [("p", "schedule", "ok")]
    assert runs(brain)[-1]["trigger"] == "schedule"


def test_a_pipeline_that_never_synced_is_left_alone(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    assert run_due(brain, now=time.time() + 100 * HOUR) == []


def test_after_a_long_sleep_there_is_one_catch_up_run_and_no_backlog(brain, synced):
    assert len(run_due(brain, now=time.time() + 40 * HOUR)) == 1      # 40 hours missed -> one run
    assert run_due(brain, now=time.time() + 60) == []                 # the catch-up reset the clock: nothing more is owed
    assert len([r for r in runs(brain) if r["trigger"] == "schedule"]) == 1


def test_a_paused_pipeline_is_not_run(brain, synced):
    cfg = load_config(brain)
    cfg.get("p").paused = True
    save_config(brain, cfg)
    assert run_due(brain, now=time.time() + 5 * HOUR) == []


def test_an_off_pipeline_is_not_run(brain, synced):
    cfg = load_config(brain)
    cfg.get("p").schedule = "off"
    save_config(brain, cfg)
    assert run_due(brain, now=time.time() + 50 * HOUR) == []


def test_a_busy_or_skipped_run_does_not_reset_the_clock(brain, synced):
    ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "cli", "result": "busy"})
    assert last_run_ts(brain, "p") == pytest.approx(time.time(), abs=60) and len(run_due(brain, now=time.time() + HOUR + 5)) == 1


def test_a_scheduled_run_that_cannot_read_the_source_deletes_nothing(brain, synced):
    shutil.rmtree(synced)
    (res,) = run_due(brain, now=time.time() + HOUR + 5)
    assert res.result == "error"
    assert sorted(p.name for p in (brain.root / "raw/p").rglob("*.md")) == ["1.md", "2.md"]


def test_a_guard_blocked_pipeline_waits_for_approval_instead_of_blocking_again_every_interval(brain, add_pipeline, make_source):
    src = make_source("a", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    for f in list(src.glob("*.md"))[:8]:
        f.unlink()
    (blocked,) = run_due(brain, now=time.time() + HOUR + 5)
    assert blocked.result == "blocked"
    assert run_due(brain, now=time.time() + 3 * HOUR) == []                 # waiting for the person, not retrying
    assert len(list((brain.root / "raw/p").rglob("*.md"))) == 10          # and nothing was deleted


# --- MD-161 AC1/AC2: a locked vault waits for the unlock, and the first tick after it runs at once -----------------------
def _log_locked_run(brain):
    ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "schedule", "result": "error", "category": "vault-locked", "errors": []})


def test_after_a_locked_run_the_pipeline_runs_again_as_soon_as_the_vault_opens(brain, synced, monkeypatch):
    from mdpipeline import scheduler

    _log_locked_run(brain)
    monkeypatch.setattr(scheduler, "vault_opens", lambda: True)
    assert [r.trigger for r in run_due(brain, now=time.time() + 60)] == ["schedule"]


def test_after_a_locked_run_it_waits_quietly_while_the_vault_stays_locked(brain, synced, monkeypatch):
    from mdpipeline import scheduler

    _log_locked_run(brain)
    monkeypatch.setattr(scheduler, "vault_opens", lambda: False)
    before = len(runs(brain))
    assert run_due(brain, now=time.time() + 60) == [] and len(runs(brain)) == before


def test_a_pipeline_whose_last_run_was_not_locked_never_asks_the_vault(brain, synced, monkeypatch):
    from mdpipeline import scheduler

    monkeypatch.setattr(scheduler, "vault_opens", lambda: (_ for _ in ()).throw(AssertionError("asked")))
    assert run_due(brain, now=time.time() + 60) == []
