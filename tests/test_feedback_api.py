"""MD-123 over HTTP: the prompt, the answer, the dismissal and the source request; the explicit sends show up in "what we send" (AC5)."""
import pytest

from mdpipeline import usage
from mdpipeline.config import Pipeline
from mdpipeline.log import ActivityLog
from test_usage_emit import Recorder
from uihelper import running_ui


@pytest.fixture
def transport(monkeypatch):
    t = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: t)
    return t


@pytest.fixture
def synced(brain, add_pipeline):
    add_pipeline(Pipeline.new("p", []))
    ActivityLog(brain).append({"type": "run", "pipeline": "p", "trigger": "ui", "result": "ok", "counts": {"created": 4}, "errors": []})


def test_the_prompt_endpoint_returns_the_due_question_once(brain, synced):
    with running_ui(brain) as ui:
        _, first = ui.get("/api/prompt")
        _, second = ui.get("/api/prompt")
    assert first["prompt"]["question_id"] == "first_sync_expectation" and second == {"prompt": None}


def test_answering_sends_it_and_it_appears_in_what_we_send_labelled_as_chosen(brain, synced, transport):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/prompt/answer", {"question_id": "first_sync_expectation", "answer": "partly", "comment": "More fields please"})
        _, shown = ui.get("/api/usage")
    assert status == 200 and res == {"ok": True}
    assert transport.events[0]["properties"]["comment"] == "More fields please"
    entry = shown["log"][0]
    assert entry["event"] == "feedback_answered" and entry["chosen"] is True and entry["properties"]["answer"] == "partly"


def test_an_unknown_answer_is_a_400(brain, synced, transport):
    with running_ui(brain) as ui:
        assert ui.post("/api/prompt/answer", {"question_id": "first_sync_expectation", "answer": "maybe"})[0] == 400
    assert transport.events == []


def test_dismissing_sends_nothing(brain, synced, transport):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/prompt/dismiss", {"question_id": "first_sync_expectation"})
    assert status == 200 and res == {"ok": True} and transport.events == []


def test_a_source_request_is_sent_with_sharing_off_and_listed_in_what_we_send(brain, transport):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/source-request", {"source_id": "other", "other_source": "Linear", "email": "dana@acme.dev"})
        _, shown = ui.get("/api/usage")
    assert status == 200 and res == {"ok": True}
    assert transport.events[0]["properties"]["other_source"] == "Linear"
    assert shown["log"][0]["event"] == "source_requested" and shown["log"][0]["chosen"] is True and shown["log"][0]["properties"]["email"] == "dana@acme.dev"


def test_an_unknown_source_is_a_400(brain, transport):
    with running_ui(brain) as ui:
        assert ui.post("/api/source-request", {"source_id": "lotus_notes"})[0] == 400


def test_a_bad_email_comes_back_as_a_message_not_an_error_status(brain, transport):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/source-request", {"source_id": "notion", "email": "nope"})
    assert status == 200 and res["ok"] is False and "email" in res["error"].lower()


def test_the_page_cannot_send_these_two_events_through_the_automatic_event_endpoint(brain, transport):
    usage.UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/usage/event", {"event": "source_requested", "properties": {"source_id": "notion"}})
    assert res == {"queued": False} and transport.events == []
