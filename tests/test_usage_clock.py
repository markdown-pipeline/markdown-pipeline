"""MD-116 (FR-S2 AC3, AC4 + product's first_agent_read): the time-to-value clock and the per-day agent read count."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mdpipeline import usage
from mdpipeline.usage import UsageStore

T0 = datetime(2026, 10, 8, 9, 0, 0, tzinfo=timezone.utc)


def at(**delta) -> datetime:
    return T0 + timedelta(**delta)


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued() -> list[dict]:
    return json.loads((usage.data_dir() / "usage.json").read_text())["queue"]


# --- first open / first sync -------------------------------------------------------------------------------------------
def test_the_first_open_is_remembered_and_only_the_first_call_says_so():
    store = UsageStore()
    assert store.touch_first_open(T0) is True
    assert store.touch_first_open(at(minutes=5)) is False
    assert UsageStore().ms_since_first_open(at(seconds=90)) == 90_000


def test_the_clock_is_kept_even_without_consent_so_a_later_opt_in_still_has_the_time():
    store = UsageStore()
    assert store.sharing is False
    store.touch_first_open(T0)
    assert store.ms_since_first_open(at(seconds=3)) == 3000


def test_before_any_open_there_is_no_time():
    assert UsageStore().ms_since_first_open(T0) is None and UsageStore().ms_since_first_sync(T0) is None


def test_the_first_sync_is_remembered_and_only_the_first_call_says_so():
    store = UsageStore()
    assert store.note_first_sync(T0) is True
    assert store.note_first_sync(at(hours=1)) is False
    assert store.ms_since_first_sync(at(seconds=340)) == 340_000


# --- agent reads: a count per day, nothing else ------------------------------------------------------------------------
def test_reads_are_not_counted_at_all_without_consent(app_data):
    assert usage.note_agent_read(T0) is False
    assert "agent_reads" not in json.loads((usage.data_dir() / "usage.json").read_text())


def test_reads_on_the_same_day_only_increase_a_counter(sharing):
    for minute in range(3):
        usage.note_agent_read(at(minutes=minute))
    events = [e["event"] for e in queued()]
    assert "agent_read" not in events                         # a day's count is sent once the day is over


def test_the_next_day_the_previous_days_count_is_queued_as_one_event_dated_to_that_day(sharing):
    for minute in range(3):
        usage.note_agent_read(at(minutes=minute))
    usage.note_agent_read(at(days=1))
    reads = [e for e in queued() if e["event"] == "agent_read"]
    assert len(reads) == 1 and reads[0]["properties"]["reads_count"] == 3
    assert reads[0]["at"].startswith("2026-10-08")             # attributed to the day the reads happened, not the day it was sent
    assert reads[0]["chosen"] is False


def test_a_day_is_sent_only_once(sharing):
    usage.note_agent_read(T0)
    usage.note_agent_read(at(days=1))
    usage.note_agent_read(at(days=1, minutes=5))
    usage.note_agent_read(at(days=2))
    counts = [e["properties"]["reads_count"] for e in queued() if e["event"] == "agent_read"]
    assert counts == [1, 2]


def test_a_gap_of_several_days_sends_each_day_that_had_reads(sharing):
    usage.note_agent_read(T0)
    usage.note_agent_read(at(days=3))
    assert len([e for e in queued() if e["event"] == "agent_read"]) == 1


def test_the_first_agent_read_is_emitted_once_with_the_time_since_the_first_sync(sharing):
    UsageStore().note_first_sync(T0)
    usage.note_agent_read(at(seconds=340))
    usage.note_agent_read(at(seconds=400))
    firsts = [e for e in queued() if e["event"] == "first_agent_read"]
    assert len(firsts) == 1 and firsts[0]["properties"]["ms_since_first_sync"] == 340_000


def test_the_first_agent_read_without_a_known_first_sync_still_counts_without_the_time(sharing):
    usage.note_agent_read(T0)
    (first,) = [e for e in queued() if e["event"] == "first_agent_read"]
    assert "ms_since_first_sync" not in first["properties"]


def test_the_agent_read_events_carry_no_content_only_the_count_and_the_globals(sharing):
    usage.note_agent_read(T0)
    usage.note_agent_read(at(days=1))
    (read,) = [e for e in queued() if e["event"] == "agent_read"]
    assert set(read["properties"]) == {"reads_count", "app_version", "deployment", "session_id", "is_internal"}


def test_noting_a_read_never_raises(sharing, monkeypatch):
    monkeypatch.setattr(UsageStore, "_write", lambda self, data: (_ for _ in ()).throw(OSError("disk full")))
    assert usage.note_agent_read(T0) is False
