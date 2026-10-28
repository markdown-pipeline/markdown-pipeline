"""MD-123: what a person types and presses Send on (a source request, a feedback answer) is the one thing that may carry words.

Those events are `explicit`: they can only be built by an explicit send, never by emit(), and never with sharing as the reason.
"""
import json

import pytest

from mdpipeline import usage
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import EVENTS, EmailAddress, UserText, sanitize

from test_usage_emit import Recorder

WORDS = "We use Linear for everything, please add it"


def test_the_two_user_events_are_explicit_and_no_other_event_is():
    assert {n for n, e in EVENTS.items() if e.explicit} == {"source_requested", "feedback_answered"}


def test_only_explicit_events_may_hold_typed_words():
    holders = {n for n, e in EVENTS.items() if any(isinstance(r, (UserText, EmailAddress)) for r in e.props.values())}
    assert holders and all(EVENTS[n].explicit for n in holders)


def test_an_explicit_event_is_refused_by_the_automatic_path():
    assert sanitize("source_requested", {"source_id": "notion"}) is None
    assert usage.emit("source_requested", source_id="notion") is False


def test_an_automatic_event_is_refused_by_the_explicit_path():
    assert sanitize("app_opened", {}, explicit=True) is None


def test_a_source_request_keeps_the_typed_tool_name_and_the_email():
    props = {"source_id": "other", "other_source": WORDS, "email": "dana@acme.dev"}
    assert sanitize("source_requested", props, explicit=True) == props


def test_typed_words_are_trimmed_and_capped():
    out = sanitize("source_requested", {"source_id": "other", "other_source": "  " + "x" * 500 + "  "}, explicit=True)
    assert len(out["other_source"]) == 100


@pytest.mark.parametrize("bad", ["", "   ", 5, None, ["a"], "a\x00b"])
def test_empty_odd_or_control_character_words_are_dropped(bad):
    assert sanitize("source_requested", {"source_id": "other", "other_source": bad}, explicit=True) == {"source_id": "other"}


@pytest.mark.parametrize("bad", ["not an email", "a@b", "@acme.dev", "a b@acme.dev", "x" * 200 + "@acme.dev", 3])
def test_a_bad_email_is_dropped(bad):
    assert sanitize("source_requested", {"source_id": "notion", "email": bad}, explicit=True) == {"source_id": "notion"}


def test_a_feedback_answer_keeps_its_comment_and_a_fixed_answer_only():
    props = {"question_id": "no_agent_read_3d", "answer": "no_time", "comment": WORDS}
    assert sanitize("feedback_answered", props, explicit=True) == props
    assert sanitize("feedback_answered", {"question_id": "no_agent_read_3d", "answer": "I typed this"}, explicit=True) == {"question_id": "no_agent_read_3d"}


def test_jira_data_center_is_a_requestable_source():
    assert sanitize("source_requested", {"source_id": "jira_data_center"}, explicit=True) == {"source_id": "jira_data_center"}


def test_the_explicit_send_carries_the_words_and_works_with_sharing_off(app_data):
    transport = Recorder()
    assert UsageStore().sharing is False
    assert usage.send_chosen("source_requested", transport, source_id="other", other_source=WORDS, email="dana@acme.dev") is True
    (event,) = transport.events
    assert event["chosen"] is True and event["properties"]["other_source"] == WORDS and event["properties"]["email"] == "dana@acme.dev"
    assert UsageStore().live_log()[-1]["properties"]["other_source"] == WORDS         # visible in "what we send"


def test_an_explicit_send_is_never_queued_for_the_batch_sender(app_data):
    usage.send_chosen("source_requested", Recorder(), source_id="notion", email="a@b.co")
    assert json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", []) == []


def test_a_failed_explicit_send_reports_false_and_logs_nothing(app_data):
    def broken(batch):
        raise OSError("offline")

    assert usage.send_chosen("source_requested", broken, source_id="notion") is False
    assert UsageStore().live_log() == []
