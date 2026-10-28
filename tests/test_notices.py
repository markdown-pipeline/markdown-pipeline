"""MD-121 AC3/4/8/9: notices (failed twice, source missing, not synced for 24 awake hours, guard waiting), one per problem."""
import shutil
import time

import pytest

from mdpipeline import notices, usage
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.notices import MAX_TICK_GAP, check, active_notices

HOUR = 3600


@pytest.fixture
def sent(monkeypatch):
    """OS notifications and usage events, recorded instead of shown / sent."""
    out = type("Sent", (), {"os": [], "events": []})()
    monkeypatch.setattr(notices, "os_notify", lambda title, body: out.os.append((title, body)))
    real_emit = usage.emit
    monkeypatch.setattr(usage, "emit", lambda name, **p: out.events.append((name, p)))
    return out


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("a", {"1.md": "one", "2.md": "two"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    return src


def kinds(brain):
    return sorted((n["pipeline"], n["kind"]) for n in active_notices(brain))


def fail(brain, n=1):
    for _ in range(n):
        ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "schedule", "result": "error",
                                   "errors": [{"source": "a", "error": "boom", "category": "network"}]})


def test_a_missing_source_folder_is_logged_with_its_own_category(brain, synced):
    shutil.rmtree(synced)
    sync_pipeline(brain, "p", trigger="schedule")
    assert ActivityLog(brain).read(types={"run"})[-1]["errors"][0]["category"] == "source-missing"


def test_no_notice_when_all_is_well(brain, synced, sent):
    assert check(brain, now=time.time()) == [] and sent.os == [] and sent.events == []


def test_one_failure_is_not_a_notice_two_in_a_row_are(brain, synced, sent):
    fail(brain)
    check(brain, now=time.time())
    assert kinds(brain) == []
    fail(brain)
    check(brain, now=time.time())
    assert kinds(brain) == [("p", "sync_failed")]


def test_a_success_between_two_failures_resets_the_count(brain, synced, sent):
    fail(brain)
    sync_pipeline(brain, "p", trigger="schedule")
    fail(brain)
    check(brain, now=time.time())
    assert kinds(brain) == []


def test_a_missing_source_is_a_notice_at_the_first_failed_run(brain, synced, sent):
    shutil.rmtree(synced)
    sync_pipeline(brain, "p", trigger="schedule")
    check(brain, now=time.time())
    assert kinds(brain) == [("p", "source_missing")]


def test_a_missing_source_is_not_also_reported_as_a_failure(brain, synced, sent):
    shutil.rmtree(synced)
    sync_pipeline(brain, "p", trigger="schedule")
    sync_pipeline(brain, "p", trigger="schedule")
    check(brain, now=time.time())
    assert kinds(brain) == [("p", "source_missing")]


def test_one_notice_per_problem_not_per_run(brain, synced, sent):
    fail(brain, 2)
    check(brain, now=time.time())
    fail(brain, 3)
    check(brain, now=time.time())
    check(brain, now=time.time())
    assert len(sent.os) == 1 and [e for e in sent.events if e[0] == "notice_shown"] == [("notice_shown", {"kind": "sync_failed"})]


def test_the_notice_is_raised_again_after_the_problem_was_fixed_and_came_back(brain, synced, sent):
    fail(brain, 2)
    check(brain, now=time.time())
    sync_pipeline(brain, "p", trigger="schedule")
    check(brain, now=time.time())
    assert kinds(brain) == []                            # fixed: the item goes away by itself
    fail(brain, 2)
    check(brain, now=time.time())
    assert len(sent.os) == 2


def test_the_os_notification_says_what_happened_and_never_names_content(brain, synced, sent):
    fail(brain, 2)
    check(brain, now=time.time())
    (title, body), = sent.os
    assert "p" in body and "boom" not in body and "/" not in body


def test_a_paused_pipeline_raises_no_notice(brain, synced, sent):
    from mdpipeline.service import set_paused

    fail(brain, 2)
    set_paused(brain, "p", True, trigger="cli")
    check(brain, now=time.time())
    assert kinds(brain) == []


def test_a_guard_blocked_run_notifies_that_a_decision_is_waiting(brain, add_pipeline, make_source, sent):
    src = make_source("g", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    for f in list(src.glob("*.md"))[:8]:
        f.unlink()
    sync_pipeline(brain, "p", trigger="schedule")
    check(brain, now=time.time())
    assert kinds(brain) == [("p", "guard_blocked")] and len(sent.os) == 1
    sync_pipeline(brain, "p", trigger="ui", approve=True)
    check(brain, now=time.time())
    assert kinds(brain) == []


# --- AC9: the stale notice counts only awake time -------------------------------------------------------------------
def tick_for(brain, start, seconds, step=240):
    t = start
    while t < start + seconds:
        t += step
        check(brain, now=t)
    return t


def test_24_awake_hours_without_a_sync_is_a_stale_notice(brain, synced, sent):
    start = time.time()
    check(brain, now=start)
    tick_for(brain, start, 24 * HOUR - 600)
    assert kinds(brain) == []
    tick_for(brain, start + 24 * HOUR - 600, 1200)
    assert kinds(brain) == [("p", "stale")]


def test_a_weekend_with_the_laptop_closed_is_not_stale_and_the_catch_up_clears_the_clock(brain, synced, sent):
    start = time.time()
    check(brain, now=start)
    tick_for(brain, start, 2 * HOUR)
    woke = start + 60 * HOUR                            # the machine was asleep for 60 hours: no tick in between
    check(brain, now=woke)
    assert kinds(brain) == [] and sent.os == []


def test_a_gap_longer_than_the_tick_limit_counts_as_sleep(brain, synced, sent):
    start = time.time()
    check(brain, now=start)
    check(brain, now=start + MAX_TICK_GAP + 1)
    assert notices.awake_seconds(brain, "p") == 0


def test_normal_ticks_count_as_awake_time(brain, synced, sent):
    start = time.time()
    check(brain, now=start)
    check(brain, now=start + 200)
    assert notices.awake_seconds(brain, "p") == 200


def test_a_new_success_resets_the_awake_clock(brain, synced, sent):
    start = time.time()
    check(brain, now=start)
    check(brain, now=start + 200)
    sync_pipeline(brain, "p", trigger="schedule")
    check(brain, now=start + 400)
    assert notices.awake_seconds(brain, "p") == 0


def test_a_scheduled_off_pipeline_is_never_stale(brain, synced, sent):
    from mdpipeline.config import load_config, save_config

    cfg = load_config(brain)
    cfg.get("p").schedule = "off"
    save_config(brain, cfg)
    start = time.time()
    check(brain, now=start)
    tick_for(brain, start, 26 * HOUR)
    assert kinds(brain) == []


# --- review fixes (MD-121 review 2026-10-09) -------------------------------------------------------------------------
def test_stale_is_not_raised_on_top_of_a_failure_notice_for_the_same_cause(brain, synced, sent):
    fail(brain, 2)
    start = time.time()
    check(brain, now=start)
    tick_for(brain, start, 25 * HOUR)
    assert kinds(brain) == [("p", "sync_failed")]
    assert len(sent.os) == 1


def test_stale_is_not_raised_while_a_blocked_run_waits_for_the_person(brain, add_pipeline, make_source, sent):
    src = make_source("g", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    for f in list(src.glob("*.md"))[:8]:
        f.unlink()
    sync_pipeline(brain, "p", trigger="schedule")
    start = time.time()
    check(brain, now=start)
    tick_for(brain, start, 25 * HOUR)
    assert kinds(brain) == [("p", "guard_blocked")]


def test_stale_is_still_raised_when_nothing_else_explains_it(brain, synced, sent):
    """The clock is not stuck on a failure: a pipeline whose runs simply stopped is stale."""
    start = time.time()
    check(brain, now=start)
    tick_for(brain, start, 25 * HOUR)
    assert kinds(brain) == [("p", "stale")]


@pytest.mark.parametrize("kind", ["sync_failed", "stale"])
def test_the_os_notification_says_nothing_was_deleted(kind):
    assert "Nothing was deleted" in notices._TEXT[kind]


def test_the_guard_notification_says_nothing_was_changed():
    assert "nothing was changed" in notices._TEXT["guard_blocked"].lower()


@pytest.mark.parametrize("kind", sorted(notices._TEXT))
def test_the_real_notification_texts_carry_only_the_pipeline_name(kind):
    text = notices._TEXT[kind].format(p='"acme"')
    assert "acme" in text and "/" not in text and "\\" not in text and "boom" not in text


def test_a_locked_vault_twice_in_a_row_is_not_a_failed_twice_notice(brain, synced, sent):
    for _ in range(2):
        ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "schedule", "result": "error", "category": "vault-locked"})
    check(brain, now=time.time())
    assert kinds(brain) == [] and sent.os == []
