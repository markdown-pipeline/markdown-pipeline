"""MD-126 AC4 (release gate): the scheduler never deletes on a failed, partial or unauthorised run, catches up once after sleep, and two
overlapping triggers never run the same pipeline twice at once. Everything against the fake Jira / real folders, no network. Run alone:

    uv run pytest tests/test_release_gate_scheduler.py

Together with tests/test_data_loss*.py (MD-30, 41, 55, 69) this is the data-loss gate, now for runs started by the clock.
"""
import threading
import time

import pytest

from mdpipeline import notices, scheduler
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.jira_source import JiraSource
from mdpipeline.log import ActivityLog
from test_data_loss_jira import curated_intact, fake, jira_files, world  # noqa: F401  (the Jira world of the MD-55 suite)

HOUR = 3600


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


def later(hours=1.0):
    return time.time() + hours * HOUR + 5


def scheduled(world, **kw):
    """The clock fires: returns the single result of the one pipeline."""
    (res,) = scheduler.run_due(world.brain, now=kw.get("now") or later())
    assert res.trigger == "schedule"
    return res


def test_s1_a_scheduled_run_during_an_api_outage_deletes_nothing_and_the_next_one_converges(world):
    assert world.sync().result == "ok"
    before = jira_files(world)
    for n in range(6, 10):
        world.fake.add_issue("MD", f"new issue {n}")
    world.fake.fail("GET", "/issue/", status=503, times=3)
    res = scheduled(world)
    assert res.result == "error" and res.counts()["deleted"] == 0 and set(before) <= set(jira_files(world))     # new files may land, none go
    assert scheduled(world, now=later(3)).result == "ok" and len(jira_files(world)) == 10
    curated_intact(world)


def test_s2_a_scheduled_run_with_a_revoked_token_deletes_nothing(world, monkeypatch):
    world.sync()
    before = jira_files(world)
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    res = scheduled(world)
    assert res.result == "error" and res.counts()["deleted"] == 0 and jira_files(world) == before
    curated_intact(world)


def test_s3_a_scheduled_run_where_one_page_out_of_n_fails_deletes_nothing(world):
    world.sync()
    before = jira_files(world)
    JiraSource.page_size = 2
    world.fake.fail_page(2)
    res = scheduled(world)
    assert res.result == "error" and res.counts()["deleted"] == 0 and jira_files(world) == before


def test_s4_a_scheduled_run_that_hits_a_rate_limit_that_never_clears_deletes_nothing(world):
    world.sync()
    before = jira_files(world)
    world.fake.rate_limit(times=999, retry_after=1)
    res = scheduled(world)
    assert res.result == "error" and res.counts()["deleted"] == 0 and jira_files(world) == before


def test_s5_a_scheduled_run_with_the_source_folder_gone_deletes_nothing(brain, add_pipeline, make_source):
    import shutil

    src = make_source("docs", {f"{i}.md": str(i) for i in range(4)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    shutil.rmtree(src)
    (res,) = scheduler.run_due(brain, now=later())
    assert res.result == "error" and res.counts()["deleted"] == 0
    assert len(list((brain.root / "raw/p").rglob("*.md"))) == 4


def test_s6_failures_repeating_every_interval_for_a_week_never_delete_and_raise_one_notice(world):
    world.sync()
    before = jira_files(world)
    world.fake.fail("GET", "/search/jql", status=503, times=9999)
    for hour in range(1, 24 * 7, 1):
        scheduler.tick(world.brain, now=later(hour))
    assert jira_files(world) == before
    assert [n["kind"] for n in notices.active_notices(world.brain) if n["kind"] == "sync_failed"] == ["sync_failed"]


def test_s7_a_scheduled_run_above_the_guard_threshold_writes_nothing_and_waits_for_a_person(world):
    world.sync()
    before = jira_files(world)
    for key in ("MD-1", "MD-2", "MD-3", "MD-4", "MD-5"):
        world.fake.hide(key)
    res = scheduled(world)
    assert res.result == "blocked" and jira_files(world) == before
    assert scheduler.run_due(world.brain, now=later(5)) == []          # not retried every interval
    assert jira_files(world) == before


# --- catch-up -----------------------------------------------------------------------------------------------------
def test_c1_after_a_simulated_sleep_of_a_week_a_pipeline_runs_once_not_once_per_missed_interval(world):
    world.sync()
    runs_before = len(ActivityLog(world.brain).read(types={"run"}))
    scheduler.tick_all(now=time.time() + 7 * 24 * HOUR)
    scheduler.tick_all(now=time.time() + 60)
    assert len(ActivityLog(world.brain).read(types={"run"})) == runs_before + 1


# --- overlapping triggers ---------------------------------------------------------------------------------------
def test_o1_a_scheduled_tick_during_a_running_manual_sync_does_not_run_the_pipeline_a_second_time(world):
    world.sync()
    inside, release = threading.Event(), threading.Event()
    results = {}

    def hook(item):
        inside.set()
        release.wait(10)

    world.fake.add_issue("MD", "something new for the manual run to write")
    manual = threading.Thread(target=lambda: results.setdefault("manual", sync_pipeline(world.brain, "p", trigger="ui", after_write=hook)))
    manual.start()
    assert inside.wait(10)
    during = scheduler.run_due(world.brain, now=later())
    release.set()
    manual.join(15)
    assert [r.result for r in during] == ["busy"] and results["manual"].result == "ok"
    runs = [r for r in ActivityLog(world.brain).read(types={"run"}) if r["result"] == "ok"]
    assert len(runs) == 2                               # the first sync and the manual one: the scheduled tick added none
    assert scheduled(world, now=later(3)).result == "ok"       # a busy answer does not move the clock: the next tick runs it


def test_o2_two_scheduled_ticks_at_once_never_run_the_pipeline_at_the_same_time(world, monkeypatch):
    """Measured where the work happens (inside the brain lock): however the two ticks interleave, never two runs at once."""
    import mdpipeline.engine as engine

    world.sync()
    active, peak, guard = [0], [0], threading.Lock()
    real = engine._run_locked

    def counted(*a, **k):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            time.sleep(0.3)
            return real(*a, **k)
        finally:
            with guard:
                active[0] -= 1

    monkeypatch.setattr(engine, "_run_locked", counted)
    out = []
    threads = [threading.Thread(target=lambda: out.extend(scheduler.run_due(world.brain, now=later()))) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert peak[0] == 1
    assert "ok" in [r.result for r in out] and set(r.result for r in out) <= {"ok", "busy"}
