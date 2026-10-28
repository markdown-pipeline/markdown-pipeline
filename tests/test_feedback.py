"""MD-123 AC2-AC5: one-question prompts at three moments (shown once, dismissible, never blocking) and the source request."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mdpipeline import feedback, usage
from mdpipeline.config import Pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.usage import UsageStore

from test_usage_emit import Recorder

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run(brain, days_ago, created=3, result="ok"):
    ActivityLog(brain).append({"ts": stamp(days_ago), "type": "run", "pipeline": "p", "trigger": "ui", "result": result,
                               "counts": {"created": created}, "errors": []})


def mcp(brain, days_ago, tool="search"):
    ActivityLog(brain).append({"ts": stamp(days_ago), "type": "mcp_call", "tool": tool})


def removed(brain, days_ago=0.1):
    ActivityLog(brain).append({"ts": stamp(days_ago), "type": "event", "action": "remove", "pipeline": "p", "trigger": "ui"})


# which prompt, when ---------------------------------------------------------------------------------------------
def test_nothing_is_asked_before_anything_happened(brain):
    assert feedback.next_prompt(brain, NOW) is None


def test_after_the_first_sync_the_expectation_question_is_asked_with_three_answers_and_an_optional_comment(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01)
    p = feedback.next_prompt(brain, NOW)
    assert p["question_id"] == "first_sync_expectation" and p["text"] == "Did you get what you expected?"
    assert [o["answer"] for o in p["options"]] == ["yes", "partly", "no"] and [o["label"] for o in p["options"]] == ["Yes", "Partly", "No"]
    assert p["comment_label"] and p["goes_to_the_team"] is True


def test_a_failed_or_empty_first_sync_asks_nothing(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01, created=0)
    run(brain, 0.01, created=5, result="error")
    assert feedback.next_prompt(brain, NOW) is None


def test_each_prompt_is_shown_once_even_if_never_answered(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01)
    assert feedback.next_prompt(brain, NOW) is not None
    assert feedback.next_prompt(brain, NOW) is None


@pytest.mark.parametrize("days,expected", [(2.9, None), (3.1, "no_agent_read_3d")])
def test_three_days_after_the_first_sync_without_an_agent_read_asks_what_stopped_you(brain, add_pipeline, days, expected):
    add_pipeline(Pipeline.new("p", []))
    run(brain, days)
    feedback.next_prompt(brain, NOW - timedelta(days=days))          # the first-sync question was already shown, back then
    p = feedback.next_prompt(brain, NOW)
    assert (p or {}).get("question_id") == expected


def test_the_what_stopped_you_options_are_fixed_and_labelled_in_plain_words(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 4)
    feedback.dismiss("first_sync_expectation")
    p = feedback.next_prompt(brain, NOW)
    assert p["text"] == "No connected agent has read your files yet. Which fits best?"
    assert [o["answer"] for o in p["options"]] == ["no_agent_installed", "reads_folder_directly", "dont_know_how", "tried_failed", "no_time", "files_not_useful", "other"]


@pytest.mark.parametrize("tool", ["search", "read", "list"])
def test_an_agent_read_means_nobody_asks_what_stopped_you(brain, add_pipeline, tool):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 4)
    mcp(brain, 1, tool)
    feedback.dismiss("first_sync_expectation")
    assert feedback.next_prompt(brain, NOW) is None


def test_removing_the_last_source_asks_why(brain):
    run(brain, 5)
    removed(brain)
    feedback.dismiss("first_sync_expectation")
    feedback.dismiss("no_agent_read_3d")
    p = feedback.next_prompt(brain, NOW)
    assert p["question_id"] == "last_source_removed" and p["text"] == "Why did you remove it?"
    assert [o["answer"] for o in p["options"]] == ["just_testing", "wrong_source", "not_useful", "privacy_worry", "broke", "other"]


def test_removing_one_of_several_sources_asks_nothing(brain, add_pipeline):
    add_pipeline(Pipeline.new("other", []))
    removed(brain)
    assert feedback.next_prompt(brain, NOW) is None


def test_only_one_prompt_at_a_time_and_the_removal_question_comes_first(brain):
    run(brain, 5)
    removed(brain)
    assert feedback.next_prompt(brain, NOW)["question_id"] == "last_source_removed"


# answering and dismissing ---------------------------------------------------------------------------------------
def test_an_answer_goes_to_the_team_as_an_explicit_send_even_with_sharing_off(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01)
    feedback.next_prompt(brain, NOW)
    t = Recorder()
    assert UsageStore().sharing is False
    res = feedback.answer("first_sync_expectation", "partly", "Missing the comments", t)
    assert res == {"ok": True}
    (event,) = t.events
    assert event["event"] == "feedback_answered" and event["chosen"] is True
    assert {k: event["properties"][k] for k in ("question_id", "answer", "comment")} == {
        "question_id": "first_sync_expectation", "answer": "partly", "comment": "Missing the comments"}


def test_an_answered_question_is_never_asked_again(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01)
    feedback.answer("first_sync_expectation", "yes", "", Recorder())
    assert feedback.next_prompt(brain, NOW) is None


def test_an_answer_that_is_not_one_of_the_options_is_refused_and_sends_nothing():
    t = Recorder()
    with pytest.raises(ValueError):
        feedback.answer("first_sync_expectation", "no_time", "", t)
    with pytest.raises(ValueError):
        feedback.answer("nope", "yes", "", t)
    assert t.events == []


def test_an_answer_without_a_comment_sends_no_comment_field():
    t = Recorder()
    feedback.answer("first_sync_expectation", "yes", "   ", t)
    assert "comment" not in t.events[0]["properties"]


def test_a_failed_send_says_so_and_keeps_the_question_open(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01)

    def offline(batch):
        raise OSError("offline")

    res = feedback.answer("first_sync_expectation", "yes", "", offline)
    assert res["ok"] is False and "offline" not in json.dumps(res).lower() and "try again" in res["error"].lower()
    assert "first_sync_expectation" not in feedback.handled()


def test_no_transport_means_nothing_is_sent_and_it_says_so():
    res = feedback.answer("first_sync_expectation", "yes", "", None)
    assert res["ok"] is False


def test_dismissing_sends_nothing_and_the_question_never_returns(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    run(brain, 0.01)
    feedback.dismiss("first_sync_expectation")
    assert feedback.next_prompt(brain, NOW) is None and UsageStore().live_log() == []
    assert feedback.handled()["first_sync_expectation"] == "dismissed"


# request a source -----------------------------------------------------------------------------------------------
def test_a_source_request_sends_the_choice_the_typed_name_and_the_email():
    t = Recorder()
    assert feedback.request_source("other", "Linear", "dana@acme.dev", t) == {"ok": True}
    (event,) = t.events
    assert event["event"] == "source_requested" and event["chosen"] is True
    assert {k: event["properties"][k] for k in ("source_id", "other_source", "email")} == {"source_id": "other", "other_source": "Linear", "email": "dana@acme.dev"}


def test_a_chip_request_needs_no_email_and_no_words():
    t = Recorder()
    assert feedback.request_source("notion", "", "", t) == {"ok": True}
    assert set(t.events[0]["properties"]) >= {"source_id"} and "email" not in t.events[0]["properties"] and "other_source" not in t.events[0]["properties"]


def test_the_typed_name_is_ignored_unless_the_choice_is_other():
    t = Recorder()
    feedback.request_source("notion", "something typed", "", t)
    assert "other_source" not in t.events[0]["properties"]


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError):
        feedback.request_source("lotus_notes", "", "", Recorder())


def test_a_bad_email_is_refused_instead_of_silently_dropped():
    t = Recorder()
    res = feedback.request_source("notion", "", "not an email", t)
    assert res["ok"] is False and "email" in res["error"].lower() and t.events == []


def test_a_failed_request_send_says_so():
    def offline(batch):
        raise OSError("x")

    assert feedback.request_source("notion", "", "", offline)["ok"] is False
