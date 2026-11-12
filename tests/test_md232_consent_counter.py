"""MD-232 (AC1-AC4, AC7, AC10-AC12, D1, D3, D5): the consent counter. One anonymous count of the FIRST answer (share / decline, never no_answer),
only for a usage store created by 0.1.24+, shown in the live log, with no install ID, no location and no timestamp."""
from __future__ import annotations

import json

import pytest

from mdpipeline import usage
from mdpipeline.usage import UsageStore, posthog_transport


@pytest.fixture(autouse=True)
def real_user(monkeypatch):
    """The test run marks itself internal (MD-154); these tests are about a real user unless one says otherwise."""
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "0")


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def wire(batch: list[dict]) -> list[dict]:
    """What posthog_transport really posts for ``batch`` (the opener is a fake: no network)."""
    sent: list[dict] = []

    def opener(request, timeout):
        sent.extend(json.loads(request.data)["batch"])
        return FakeResponse()

    posthog_transport("phc_test", opener=opener)(batch)
    return sent


def old_store(app_data, **content) -> None:
    """A usage.json written by 0.1.22 or earlier: it has no ``counter_eligible`` key."""
    (app_data.dir / "usage.json").write_text(json.dumps(content), encoding="utf-8")


# --- AC1 / AC12: exactly one count, exactly this JSON -------------------------------------------------------------------
@pytest.mark.parametrize("choice", ["share", "decline"])
def test_a_fresh_install_sends_exactly_one_count_with_exactly_this_json(choice):
    transport = Recorder()
    UsageStore().decide(choice, transport)
    counts = [e for e in transport.events if e["event"] == "consent_outcome"]
    assert len(counts) == 1
    assert wire(counts) == [{"event": "consent_outcome", "distinct_id": "anonymous-counter",
                             "properties": {"outcome": choice, "is_internal": False, "$process_person_profile": False, "$geoip_disable": True}}]


def test_the_count_says_it_is_internal_on_an_internal_install(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    transport = Recorder()
    UsageStore().decide("decline", transport)
    assert wire(transport.events) == [{"event": "consent_outcome", "distinct_id": "anonymous-counter",
                                       "properties": {"outcome": "decline", "is_internal": True, "$process_person_profile": False, "$geoip_disable": True}}]


def test_the_count_has_no_install_id_no_version_no_session_and_no_timestamp():
    transport = Recorder()
    UsageStore().decide("decline", transport)
    (item,) = wire(transport.events)
    assert set(item) == {"event", "distinct_id", "properties"}
    assert not {"app_version", "session_id", "install_id", "deployment", "timestamp"} & set(item["properties"])
    assert UsageStore().install_id not in json.dumps(item)


# --- AC2 / D3: no_answer is never sent -----------------------------------------------------------------------------------
def test_carrying_on_without_choosing_sends_nothing():
    transport = Recorder()
    UsageStore().decide("no_answer", transport)
    assert transport.events == []


def test_the_counter_refuses_no_answer_even_when_called_directly():
    transport = Recorder()
    assert UsageStore().report_consent_outcome("no_answer", transport) is False
    assert transport.events == []


# --- AC3: only the first decision is counted -----------------------------------------------------------------------------
def test_changing_your_mind_later_sends_nothing_more():
    store, transport = UsageStore(), Recorder()
    store.decide("decline", transport)
    store.decide("share", transport)
    store.decide("decline", transport)
    assert [e["properties"]["outcome"] for e in transport.events if e["event"] == "consent_outcome"] == ["decline"]


def test_a_skipped_card_does_not_use_up_the_count_so_the_first_real_answer_is_counted():
    store, transport = UsageStore(), Recorder()
    store.decide("no_answer", transport)
    store.decide("decline", transport)
    assert [e["properties"]["outcome"] for e in transport.events] == ["decline"]


# --- AC4 / D1: the old promise is kept ------------------------------------------------------------------------------------
@pytest.mark.parametrize("content", [{"consent": "share", "install_id": "abc"}, {"consent": "decline"}, {"consent": "unanswered", "install_id": "abc"},
                                     {"install_id": "abc"}, {}])
def test_a_store_made_before_0_1_23_never_sends_the_count(app_data, content):
    old_store(app_data, **content)
    transport = Recorder()
    store = UsageStore()
    assert store.counter_eligible is False
    store.decide("decline", transport)
    assert [e for e in transport.events if e["event"] == "consent_outcome"] == []


def test_an_old_store_that_is_written_to_again_does_not_become_eligible(app_data):
    old_store(app_data, consent="decline")
    store = UsageStore()
    store.mark_notice_seen("x")
    store.set_consent("unanswered")
    assert store.counter_eligible is False


def test_a_damaged_store_is_treated_as_old(app_data):
    (app_data.dir / "usage.json").write_text("{not json", encoding="utf-8")
    assert UsageStore().counter_eligible is False


def test_a_store_created_now_is_eligible_before_and_after_its_first_write():
    assert UsageStore().counter_eligible is True
    store = UsageStore()
    store.mark_notice_seen("x")
    assert UsageStore().counter_eligible is True
    store.set_consent("unanswered")
    assert UsageStore().counter_eligible is True


def test_the_first_answer_by_command_counts_like_the_card():
    transport = Recorder()
    UsageStore().decide("share", transport)         # `mdpipeline usage on` calls decide("share")
    assert [e["properties"]["outcome"] for e in transport.events if e["event"] == "consent_outcome"] == ["share"]


# --- AC7 / D5: the live log, offline, never queued ------------------------------------------------------------------------
def test_the_count_shows_in_the_live_log_without_an_install_id():
    store = UsageStore()
    store.decide("decline", Recorder())
    (entry,) = store.view()["log"]
    assert entry["event"] == "consent_outcome" and entry["properties"] == {"outcome": "decline", "is_internal": False}
    assert entry["counter"] is True and "install_id" not in entry and entry["chosen"] is False


def test_a_failed_count_is_not_logged_not_queued_and_does_not_raise():
    def offline(batch):
        raise OSError("offline")

    store = UsageStore()
    store.decide("decline", offline)
    assert store.view()["log"] == [] and store.pending == 0


def test_a_failed_count_is_tried_again_only_at_the_next_answer_not_in_a_loop():
    calls = []

    def offline(batch):
        calls.append(batch)
        raise OSError("offline")

    store = UsageStore()
    store.decide("share", offline)
    assert len(calls) == 1                                  # no hidden retry
    store.decide("share", Recorder())
    assert UsageStore().view()["log"][0]["event"] == "consent_outcome"


def test_the_count_is_never_put_in_the_queue_with_the_identified_events():
    store = UsageStore()
    store.decide("share", Recorder())
    store.record("first_sync_completed", {"source_type": "jira"})
    assert "consent_outcome" not in [e["event"] for e in store._queue(store._read())]


def test_decide_says_whether_a_count_was_sent_by_this_call():
    store = UsageStore()
    assert store.decide("no_answer", Recorder()) is False
    assert store.decide("decline", Recorder()) is True
    assert store.decide("share", Recorder()) is False


def test_without_a_transport_nothing_is_counted_and_the_answer_is_still_kept():
    store = UsageStore()
    assert store.decide("decline", None) is False
    assert store.consent == "decline"


# --- AC11: every app event carries $geoip_disable ------------------------------------------------------------------------
def test_every_identified_event_carries_geoip_disable_and_no_person_profile():
    store = UsageStore()
    store.set_consent("share")
    store.record("first_sync_completed", {"source_type": "jira"})
    batch = store._queue(store._read())
    (item,) = wire(batch)
    assert item["properties"]["$geoip_disable"] is True and item["properties"]["$process_person_profile"] is False


# --- AC10: no automated test can reach the real analytics host -------------------------------------------------------------
def test_the_test_suite_has_no_real_transport():
    assert usage.get_transport() is None
    assert usage.default_transport() is None             # conftest sets MDPIPELINE_POSTHOG_KEY="" too, so a subprocess has none either
