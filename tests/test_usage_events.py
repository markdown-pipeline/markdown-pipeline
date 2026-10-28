"""MD-115 (FR-S1 AC2, AC3, AC4, AC7): events are queued and sent only with consent, in batches, never failing the caller."""
from __future__ import annotations

import pytest

from mdpipeline.usage import BATCH_SIZE, LOG_LIMIT, QUEUE_LIMIT, UsageStore


class FakeTransport:
    """Records every batch it is asked to send; can be told to fail."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.batches: list[list[dict]] = []
        self.fail = fail

    def __call__(self, batch: list[dict]) -> None:
        self.batches.append(batch)
        if self.fail:
            raise self.fail

    @property
    def calls(self) -> int:
        return len(self.batches)


@pytest.fixture
def sharing_store() -> UsageStore:
    store = UsageStore()
    store.set_consent("share")
    return store


@pytest.mark.parametrize("choice", ["unanswered", "decline"])
def test_without_consent_nothing_is_queued_and_the_transport_is_never_called(choice):
    store = UsageStore()
    if choice == "decline":
        store.set_consent("decline")
    transport = FakeTransport()
    assert store.record("sync_completed", {"duration_ms": 5}) is False
    assert store.flush(transport) == 0
    assert transport.calls == 0
    assert store.pending == 0


def test_with_consent_an_event_is_queued_then_sent_with_install_id(sharing_store):
    transport = FakeTransport()
    assert sharing_store.record("sync_completed", {"duration_ms": 5}) is True
    assert sharing_store.pending == 1
    assert sharing_store.flush(transport) == 1
    (batch,) = transport.batches
    assert batch[0]["event"] == "sync_completed"
    assert batch[0]["properties"] == {"duration_ms": 5}
    assert batch[0]["install_id"] == sharing_store.install_id
    assert sharing_store.pending == 0


def test_events_are_sent_in_batches(sharing_store):
    for i in range(BATCH_SIZE * 2 + 1):
        sharing_store.record("e", {"n": i})
    transport = FakeTransport()
    assert sharing_store.flush(transport) == BATCH_SIZE * 2 + 1
    assert [len(b) for b in transport.batches] == [BATCH_SIZE, BATCH_SIZE, 1]


def test_a_failing_transport_never_raises_and_keeps_the_events_for_later(sharing_store):
    sharing_store.record("e")
    assert sharing_store.flush(FakeTransport(fail=OSError("offline"))) == 0
    assert sharing_store.pending == 1
    assert sharing_store.flush(FakeTransport()) == 1


def test_a_failed_batch_stops_the_flush_instead_of_hammering(sharing_store):
    for i in range(BATCH_SIZE * 2):
        sharing_store.record("e", {"n": i})
    transport = FakeTransport(fail=TimeoutError())
    sharing_store.flush(transport)
    assert transport.calls == 1


def test_the_queue_is_capped_so_an_offline_install_does_not_grow_forever(sharing_store):
    for i in range(QUEUE_LIMIT + 10):
        sharing_store.record("e", {"n": i})
    assert sharing_store.pending == QUEUE_LIMIT


def test_declining_drops_what_was_queued(sharing_store):
    sharing_store.record("e")
    sharing_store.set_consent("decline")
    transport = FakeTransport()
    assert sharing_store.pending == 0
    assert sharing_store.flush(transport) == 0
    assert transport.calls == 0


def test_the_live_log_shows_the_last_50_sent_events_with_name_and_properties(sharing_store):
    for i in range(LOG_LIMIT + 5):
        sharing_store.record("e", {"n": i})
    sharing_store.flush(FakeTransport())
    log = sharing_store.live_log()
    assert len(log) == LOG_LIMIT
    assert log[-1]["event"] == "e" and log[-1]["properties"] == {"n": LOG_LIMIT + 4}
    assert log[0]["properties"] == {"n": 5}
    assert all(not entry["chosen"] for entry in log)


def test_events_that_failed_to_send_are_not_in_the_live_log(sharing_store):
    sharing_store.record("e")
    sharing_store.flush(FakeTransport(fail=OSError()))
    assert sharing_store.live_log() == []


def test_a_user_chosen_send_goes_out_even_when_sharing_is_off_and_is_labelled():
    store = UsageStore()
    store.set_consent("decline")
    transport = FakeTransport()
    assert store.send_user_chosen("source_requested", {"source_id": "notion"}, transport) is True
    assert transport.calls == 1
    (entry,) = store.live_log()
    assert entry["event"] == "source_requested" and entry["chosen"] is True


def test_a_failing_user_chosen_send_never_raises():
    store = UsageStore()
    assert store.send_user_chosen("source_requested", {}, FakeTransport(fail=OSError())) is False
    assert store.live_log() == []


def test_schedule_events_are_marked_as_emitted_now_and_notices_come_from_the_background_process_too():
    from mdpipeline.usage_events import EVENTS

    assert EVENTS["schedule_changed"].ships_with == "" and EVENTS["notice_shown"].ships_with == ""
    assert "engine" in EVENTS["notice_shown"].surfaces
