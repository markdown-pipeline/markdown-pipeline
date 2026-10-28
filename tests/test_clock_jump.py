"""MD-170: the system clock jumps (a dual-boot / localtime-RTC machine runs hours ahead until NTP corrects it). A sync stamped in the
future must not delay the next scheduled sync, and no age shown anywhere may be negative.

H-CLOCK-1 (product's hypothesis): ``is_due`` with ``last`` later than ``now`` waits about the skew plus the interval (3 h skew + 1 h interval
= ~4 h instead of 1 h). ``test_h_clock_1_...`` measures it; the rest pin the fix."""
import time

import pytest

from mdpipeline import log, notices, scheduler
from mdpipeline.cli import human_age
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.schedule import is_due, next_sync
from mdpipeline.status import brain_status, parse_ts

HOUR = 3600
SKEW = 3 * HOUR


@pytest.fixture(autouse=True)
def no_os_notifications(monkeypatch):
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


def hourly() -> Pipeline:
    p = Pipeline.new("p", ["/x"])
    p.schedule = "1h"
    return p


@pytest.fixture
def synced_in_the_future(brain, add_pipeline, make_source, monkeypatch):
    """A brain whose only sync was stamped SKEW seconds ahead of the real clock (the clock then went back)."""
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    real_now = log.now_iso
    monkeypatch.setattr(log, "now_iso", lambda: time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() + SKEW)))
    sync_pipeline(brain, "p", trigger="cli")
    monkeypatch.setattr(log, "now_iso", real_now)
    stamp = parse_ts(ActivityLog(brain).read(types={"run"})[-1]["ts"])
    assert stamp > time.time() + SKEW - 60                      # the fixture really is in the future
    return brain, stamp


# --- pure rules --------------------------------------------------------------------------------------------------------------------------
def test_normal_case_a_pipeline_is_due_once_the_interval_has_passed_and_not_before():
    p, last = hourly(), 1_000_000.0
    assert is_due(p, last, last + HOUR - 1) is False
    assert is_due(p, last, last + HOUR) is True


def test_a_clock_that_jumped_forward_makes_the_pipeline_due_once_not_a_backlog():
    p, last = hourly(), 1_000_000.0
    assert is_due(p, last, last + 10 * HOUR) is True
    assert next_sync(p, last, last + 10 * HOUR) == last + 10 * HOUR          # one catch-up run, now


def test_a_clock_that_jumped_back_3_hours_makes_the_pipeline_due_on_the_next_tick():
    """AC1."""
    p, last = hourly(), 1_000_000.0
    assert is_due(p, last, last - SKEW) is True


def test_a_clock_that_jumped_back_shows_the_next_sync_as_now_not_hours_away():
    p, last = hourly(), 1_000_000.0
    assert next_sync(p, last, last - SKEW) == last - SKEW


def test_a_paused_or_off_pipeline_stays_not_due_even_with_a_future_stamp():
    p, last = hourly(), 1_000_000.0
    p.paused = True
    assert is_due(p, last, last - SKEW) is False
    q = hourly()
    q.schedule = "off"
    assert is_due(q, last, last - SKEW) is False and next_sync(q, last, last - SKEW) is None


# --- H-CLOCK-1, measured through the real scheduler ------------------------------------------------------------------------------------------
def test_h_clock_1_the_next_scheduled_sync_does_not_wait_by_the_skew(synced_in_the_future):
    """The hypothesis predicts the first tick that syncs is ~SKEW + 1 h away. Ticks every 5 minutes of the real clock: the sync must come on the
    very first tick (the last run is in the future, so it is due), not hours later."""
    brain, stamp = synced_in_the_future
    start = time.time()
    first_sync_after = None
    for minutes in range(0, 5 * 60 + 1, 5):             # up to 5 h ahead
        if scheduler.run_due(brain, now=start + minutes * 60):
            first_sync_after = minutes
            break
    assert first_sync_after is not None, f"no scheduled sync within 5 h of a last run stamped {SKEW // HOUR} h in the future"
    assert first_sync_after == 0, f"the next scheduled sync waited {first_sync_after} min (skew {SKEW // 60} min + interval {HOUR // 60} min)"


# --- ages are never negative -----------------------------------------------------------------------------------------------------------
def test_status_ages_are_never_negative_when_the_last_success_is_in_the_future(synced_in_the_future):
    """AC2: `mdpipeline status` printed `age: -10735s`."""
    brain, stamp = synced_in_the_future
    data = brain_status(brain, now=time.time())
    assert data["pipelines"][0]["age_seconds"] == 0
    assert data["brain_age_seconds"] == 0


def test_status_next_sync_for_a_future_last_run_is_now(synced_in_the_future):
    brain, stamp = synced_in_the_future
    now = time.time()
    assert brain_status(brain, now=now)["pipelines"][0]["next_sync"] == now


@pytest.mark.parametrize("seconds, shown", [(-10735, "0s"), (-1, "0s"), (0, "0s"), (59, "59s"), (60, "1m"), (3600, "1h")])
def test_the_cli_age_is_never_negative(seconds, shown):
    assert human_age(seconds) == shown


def test_the_cli_status_never_prints_a_negative_age(synced_in_the_future, capsys):
    from mdpipeline.cli import main

    brain, stamp = synced_in_the_future
    main(["--brain", str(brain.root), "status"])
    out = capsys.readouterr().out
    assert "age:" in out and "-" not in out.split("age:")[1].split("\n")[0].split("since")[0]
    assert "output folder age: 0s" in out


def test_the_cli_schedule_status_never_prints_a_negative_last_sync(synced_in_the_future, capsys):
    from mdpipeline.cli import main

    brain, stamp = synced_in_the_future
    main(["--brain", str(brain.root), "schedule", "show"])
    out = capsys.readouterr().out
    assert "last sync 0s ago" in out and "ago" in out and "-" not in out.split("last sync")[1].split("ago")[0]


# --- notices -------------------------------------------------------------------------------------------------------------------------
def _watch(brain, hours, start):
    """Ticks every 5 minutes (the awake clock counts gaps up to MAX_TICK_GAP) for ``hours`` hours; returns the notice kinds at the end."""
    for i in range(0, int(hours * 12) + 1):
        notices.check(brain, now=start + i * 300)
    return {n["kind"] for n in notices.active_notices(brain)}


def test_a_future_stamp_does_not_raise_the_stale_notice_early_and_does_not_hide_it_either(synced_in_the_future):
    """AC3: "not synced for 24 h" counts awake time since the last success; a last success stamped in the future is neither 'newer than
    anything' (never stale) nor 'older' (stale at once)."""
    brain, stamp = synced_in_the_future
    t = time.time()
    assert _watch(brain, 23, t) == set()
    assert _watch(brain, 2, t + 23 * HOUR + 300) == {"stale"}


def test_the_normal_case_is_stale_after_24_awake_hours_and_not_before(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    t = time.time()
    assert _watch(brain, 23, t) == set()
    assert _watch(brain, 2, t + 23 * HOUR + 300) == {"stale"}


# --- T1 (product, 10747): the catch-up sync must not turn into a sync loop ---------------------------------------------------------------
@pytest.mark.parametrize("outcome", ["ok", "auth"])
def test_after_the_catch_up_sync_of_a_future_stamp_the_next_ticks_are_not_due_until_the_interval_has_passed(synced_in_the_future, monkeypatch, outcome):
    """A future stamp makes the pipeline due ONCE. The catch-up run is stamped with the corrected clock, so the following ticks (every minute,
    like the background process) must not sync again until a full interval later, whether the catch-up succeeded or failed with `auth`.
    Otherwise the skew would turn into hours of one sync per tick against the source's API."""
    from mdpipeline import sources

    brain, stamp = synced_in_the_future
    if outcome == "auth":
        def refuse(self, include, exclude, known=None):
            raise sources.SourceError("401 Unauthorized", category="auth")

        monkeypatch.setattr(sources.LocalFolderSource, "scan", refuse)
    start = time.time()
    catch_up = scheduler.run_due(brain, now=start)
    assert len(catch_up) == 1
    assert catch_up[0].result == ("ok" if outcome == "ok" else "error")
    last = ActivityLog(brain).read(types={"run"})[-1]
    assert last["trigger"] == "schedule" and parse_ts(last["ts"]) < stamp          # stamped with the corrected clock, not the skewed one
    runs_after_catch_up = len(ActivityLog(brain).read(types={"run"}))
    for minute in range(1, 60):                                                    # a tick a minute for the rest of the hour
        assert scheduler.run_due(brain, now=start + minute * 60) == [], f"synced again at minute {minute}"
    assert len(ActivityLog(brain).read(types={"run"})) == runs_after_catch_up
    assert len(scheduler.run_due(brain, now=start + HOUR + 60)) == 1               # and due again once the interval has passed
