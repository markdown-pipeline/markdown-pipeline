"""MD-115 (FR-S1 AC1, AC3, AC6, AC8): deciding, the one-time notices, and the view shown in the UI and the CLI."""
from __future__ import annotations

import pytest

from mdpipeline.usage import NEVER_SENT, SENT_ON_PURPOSE, WHAT_WE_SEND, UsageStore


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


@pytest.mark.parametrize("choice", ["share", "decline"])
def test_deciding_stores_the_choice(choice):
    store = UsageStore()
    store.decide(choice)
    assert UsageStore().consent == choice


def test_continuing_without_an_answer_keeps_it_unanswered_and_not_sharing():
    store = UsageStore()
    store.decide("no_answer")
    assert store.consent == "unanswered" and store.sharing is False


def test_an_unknown_decision_is_rejected():
    with pytest.raises(ValueError):
        UsageStore().decide("maybe")


@pytest.mark.parametrize("choice,outcome", [("share", "share"), ("decline", "decline"), ("no_answer", "no_answer")])
def test_the_first_decision_is_counted_once_when_the_counter_is_on(choice, outcome):
    store = UsageStore()
    store.set_counter_enabled(True)
    transport = Recorder()
    store.decide(choice, transport)
    assert [e["properties"]["outcome"] for e in transport.events] == [outcome]


def test_later_decisions_are_not_counted_so_the_rate_stays_shares_over_all_who_saw_the_card():
    store = UsageStore()
    store.set_counter_enabled(True)
    transport = Recorder()
    store.decide("no_answer", transport)
    store.decide("share", transport)
    store.decide("decline", transport)
    assert len(transport.events) == 1


def test_nothing_is_counted_while_the_counter_is_off_and_it_is_retried_once_it_is_switched_on():
    store = UsageStore()
    transport = Recorder()
    store.decide("share", transport)
    assert transport.events == []                       # off by default: not counted, and not remembered as counted
    store.set_counter_enabled(True)
    store.decide("decline", transport)
    assert len(transport.events) == 1


def test_a_failed_count_is_not_remembered_so_it_can_be_retried():
    store = UsageStore()
    store.set_counter_enabled(True)

    def boom(batch):
        raise OSError("offline")

    store.decide("share", boom)
    transport = Recorder()
    store.decide("share", transport)
    assert len(transport.events) == 1


def test_one_time_notices_are_shown_once():
    store = UsageStore()
    assert store.notice_seen("demo_one") is False
    store.mark_notice_seen("demo_one")
    assert UsageStore().notice_seen("demo_one") is True
    assert UsageStore().notice_seen("demo_two") is False


def test_the_view_for_a_new_install_says_not_chosen_and_shows_the_lists_and_an_empty_log():
    view = UsageStore().view()
    assert view["consent"] == "unanswered" and view["sharing"] is False
    assert view["what_we_send"] == list(WHAT_WE_SEND) and view["never_sent"] == list(NEVER_SENT)
    assert view["sent_on_purpose"] == SENT_ON_PURPOSE
    assert view["log"] == []
    assert view["install_id"]


def test_the_view_lists_the_log_newest_first_and_marks_what_the_user_chose_to_send():
    store = UsageStore()
    store.set_consent("share")
    store.record("first_sync_completed", {"source_type": "jira"})
    store.flush(Recorder())
    store.send_user_chosen("source_requested", {"source_id": "notion"}, Recorder())
    log = store.view()["log"]
    assert [e["event"] for e in log] == ["source_requested", "first_sync_completed"]
    assert log[0]["chosen"] is True and log[1]["chosen"] is False


def test_the_never_sent_list_names_tokens_and_content():
    text = " ".join(NEVER_SENT).lower()
    for word in ("content", "titles", "file", "paths", "tokens", "emails"):
        assert word in text


def test_the_view_never_exposes_the_queue_or_the_counter_switch():
    view = UsageStore().view()
    assert "queue" not in view and "counter_enabled" not in view
