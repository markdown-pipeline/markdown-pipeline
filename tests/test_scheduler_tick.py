"""MD-121 AC2/AC3: the background tick: which brains it serves, catch-up before notices, one broken brain never stops the others."""
import time

import pytest

from mdpipeline import notices, scheduler
from mdpipeline.brain import init_brain
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.schedulestore import SchedulerStore

HOUR = 3600


@pytest.fixture(autouse=True)
def no_os_notifications(monkeypatch):
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    return brain


def test_the_first_real_sync_registers_the_brain_for_scheduled_sync(synced):
    assert SchedulerStore().read()["brains"] == [str(synced.root)]


def test_a_dry_run_does_not_register_the_brain(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert SchedulerStore().read()["brains"] == []


def test_registering_twice_keeps_one_entry(synced):
    scheduler.register_brain(synced)
    scheduler.register_brain(synced)
    assert SchedulerStore().read()["brains"] == [str(synced.root)]


def test_tick_all_runs_what_is_due_in_every_registered_brain(synced):
    scheduler.tick_all(now=time.time() + HOUR + 5)
    assert ActivityLog(synced).read(types={"run"})[-1]["trigger"] == "schedule"


def test_tick_all_does_nothing_when_background_sync_is_turned_off(synced):
    scheduler.set_background(False)
    scheduler.tick_all(now=time.time() + 5 * HOUR)
    assert [r["trigger"] for r in ActivityLog(synced).read(types={"run"})] == ["cli"]


def test_a_brain_that_disappeared_is_skipped_without_stopping_the_others(synced, tmp_path):
    gone = init_brain(tmp_path / "gone", create=True)
    scheduler.register_brain(gone)
    import shutil

    shutil.rmtree(gone.root)
    results = scheduler.tick_all(now=time.time() + HOUR + 5)
    assert [r.pipeline for r in results] == ["p"]


def test_a_brain_whose_folder_no_longer_exists_is_dropped_from_the_registry_and_its_runtime(synced, tmp_path):
    """MD-169 (comment 10713): test brains left in /tmp failed every hour for ever and polluted sync_failed."""
    import shutil

    gone = init_brain(tmp_path / "gone", create=True)
    scheduler.register_brain(gone)
    with SchedulerStore().update() as data:
        data["runtime"][str(gone.root)] = {"last_tick": 1, "awake": {}, "notices": {}}
    shutil.rmtree(gone.root)
    scheduler.tick_all(now=time.time() + HOUR + 5)
    data = SchedulerStore().read()
    assert data["brains"] == [str(synced.root)] and str(gone.root) not in data["runtime"]


# MD-169 A1 (product, 10745): a missing folder is a grace period, not a verdict. A brain on an unmounted drive must come back by itself.
DAY = 86400


@pytest.fixture
def not_temp(monkeypatch, tmp_path):
    """The test's brain lives under the system temp dir, which would be dropped at once: pretend the temp dir is elsewhere."""
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "some-other-tmp"))


def away(brain):
    gone = brain.root.with_name(brain.root.name + ".away")
    brain.root.rename(gone)
    return gone


def runs(brain):
    return ActivityLog(brain).read(types={"run"})


def test_a_brain_whose_folder_is_missing_for_one_tick_keeps_its_registration_and_is_not_failed_or_retried(synced, not_temp):
    t = time.time() + HOUR + 5
    gone = away(synced)
    assert scheduler.tick_all(now=t) == []
    assert SchedulerStore().read()["brains"] == [str(synced.root)]
    gone.rename(synced.root)
    assert [r["result"] for r in runs(synced)] == ["ok"]                      # nothing was tried, so nothing failed
    assert notices.active_notices(synced) == []


def test_the_brain_syncs_again_on_the_next_tick_once_its_folder_is_back(synced, not_temp):
    t = time.time() + HOUR + 5
    gone = away(synced)
    scheduler.tick_all(now=t)
    gone.rename(synced.root)
    results = scheduler.tick_all(now=t + 60)
    assert [r.pipeline for r in results] == ["p"] and runs(synced)[-1]["trigger"] == "schedule"


def test_the_clock_of_the_grace_period_is_cleared_when_the_folder_returns(synced, not_temp):
    t = time.time() + HOUR + 5
    gone = away(synced)
    scheduler.tick_all(now=t)
    gone.rename(synced.root)
    scheduler.tick_all(now=t + 60)
    assert "missing_since" not in SchedulerStore().read()["runtime"].get(str(synced.root), {})
    away(synced)                                                                # gone again: a NEW grace period starts here, not at t
    scheduler.tick_all(now=t + 6 * DAY)
    scheduler.tick_all(now=t + 8 * DAY)                                         # 8 days after the first miss, only 2 after this one
    assert SchedulerStore().read()["brains"] == [str(synced.root)]


def test_a_folder_missing_for_less_than_seven_days_is_kept(synced, not_temp):
    t = time.time()
    away(synced)
    scheduler.tick_all(now=t)
    scheduler.tick_all(now=t + 7 * DAY - 60)
    assert SchedulerStore().read()["brains"] == [str(synced.root)]


def test_a_folder_missing_continuously_for_eight_days_is_dropped_with_its_bookkeeping(synced, not_temp):
    t = time.time()
    away(synced)
    scheduler.tick_all(now=t)
    scheduler.tick_all(now=t + 8 * DAY)
    data = SchedulerStore().read()
    assert data["brains"] == [] and str(synced.root) not in data["runtime"]


def test_a_missing_brain_under_the_system_temp_dir_is_dropped_at_once(synced):
    away(synced)                                       # tmp_path is under the real temp dir
    scheduler.tick_all(now=time.time())
    assert SchedulerStore().read()["brains"] == []


def test_a_brain_whose_folder_exists_but_has_no_config_yet_is_kept(synced, tmp_path):
    """Only a missing folder is forgotten: a half-set-up brain (or one being restored) must not be dropped."""
    half = init_brain(tmp_path / "half", create=True)
    scheduler.register_brain(half)
    half.config_path.unlink()
    scheduler.tick_all(now=time.time() + HOUR + 5)
    assert str(half.root) in SchedulerStore().read()["brains"]


def test_a_brain_whose_tick_raises_does_not_stop_the_others(synced, tmp_path, monkeypatch):
    other = init_brain(tmp_path / "other", create=True)
    scheduler.register_brain(other)
    real = scheduler.tick

    def flaky(brain, now=None):
        if brain.root == other.root:
            raise RuntimeError("boom")
        return real(brain, now=now)

    monkeypatch.setattr(scheduler, "tick", flaky)
    assert len(scheduler.tick_all(now=time.time() + HOUR + 5)) == 1


def test_the_catch_up_runs_before_the_notices_are_checked(synced):
    """A long sleep: the due pipeline syncs first, so no stale notice can fire for time that was only sleep."""
    start = time.time()
    scheduler.tick(synced, now=start)
    scheduler.tick(synced, now=start + 60 * HOUR)
    assert notices.active_notices(synced) == []
    assert ActivityLog(synced).read(types={"run"})[-1]["trigger"] == "schedule"


def test_set_background_off_is_remembered_and_back_on(app_data):
    scheduler.set_background(False)
    assert scheduler.background_enabled() is False
    scheduler.set_background(True)
    assert scheduler.background_enabled() is True
