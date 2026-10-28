"""MD-115 (FR-S1 AC8 + transport): the consent counter is off by default and identifier-free; PostHog transport posts a batch."""
from __future__ import annotations

import json

import pytest

from mdpipeline.usage import POSTHOG_HOST, UsageStore, posthog_transport


class Recorder:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def __call__(self, batch: list[dict]) -> None:
        self.batches.append(batch)


def test_the_consent_counter_is_off_by_default_and_sends_nothing():
    store = UsageStore()
    transport = Recorder()
    assert store.counter_enabled is False
    assert store.report_consent_outcome("decline", transport) is False
    assert transport.batches == []


@pytest.mark.parametrize("outcome", ["share", "decline", "no_answer"])
def test_with_the_counter_on_each_outcome_is_sent_without_any_identifier(outcome):
    store = UsageStore()
    store.set_counter_enabled(True)
    transport = Recorder()
    assert store.report_consent_outcome(outcome, transport) is True
    (event,) = transport.batches[0]
    assert event["event"] == "consent_outcome"
    assert event["properties"] == {"outcome": outcome}
    assert "install_id" not in event
    assert "at" not in event


def test_an_unknown_consent_outcome_is_rejected():
    store = UsageStore()
    store.set_counter_enabled(True)
    with pytest.raises(ValueError):
        store.report_consent_outcome("maybe", Recorder())


def test_the_counter_never_raises_when_offline():
    store = UsageStore()
    store.set_counter_enabled(True)

    def boom(batch):
        raise OSError("offline")

    assert store.report_consent_outcome("share", boom) is False


def test_the_counter_is_not_in_the_live_log_it_is_not_a_per_install_event():
    store = UsageStore()
    store.set_counter_enabled(True)
    store.report_consent_outcome("share", Recorder())
    assert store.live_log() == []


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_posthog_transport_posts_one_batch_to_the_batch_endpoint_with_the_key():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return FakeResponse()

    send = posthog_transport("phc_test", opener=opener)
    send([{"event": "sync_completed", "properties": {"duration_ms": 5}, "install_id": "abc", "chosen": False, "at": "2026-10-08T10:00:00Z"}])
    assert seen["url"] == POSTHOG_HOST + "/batch/"
    assert seen["body"]["api_key"] == "phc_test"
    (item,) = seen["body"]["batch"]
    assert item["event"] == "sync_completed"
    assert item["distinct_id"] == "abc"
    assert item["properties"]["duration_ms"] == 5
    assert item["properties"]["$process_person_profile"] is False       # anonymous: no person profile is built
    assert seen["timeout"] <= 5                                         # a slow host cannot hold anything up for long


def test_everything_the_transport_adds_is_disclosed_in_the_always_sent_note():
    """The live log shows each event's own properties; what the transport adds to every event must be stated next to it, not hidden."""
    from mdpipeline.usage import ALWAYS_SENT

    seen = {}

    def opener(request, timeout):
        seen["body"] = json.loads(request.data)
        return FakeResponse()

    posthog_transport("phc_test", opener=opener)([{"event": "e", "properties": {"n": 1}, "install_id": "abc", "chosen": False, "at": "2026-10-08T10:00:00Z"}])
    (item,) = seen["body"]["batch"]
    added = set(item["properties"]) - {"n"}
    assert added == {"$process_person_profile"}                 # everything else on an event is in the log (the globals are added when it is emitted)
    assert "process_person_profile" in ALWAYS_SENT and "install ID" in ALWAYS_SENT
    for shown in ("app version", "session ID", "deployment"):
        assert shown in ALWAYS_SENT


def test_the_view_carries_the_always_sent_note():
    from mdpipeline.usage import ALWAYS_SENT

    assert UsageStore().view()["always_sent"] == ALWAYS_SENT


def test_posthog_transport_sends_the_counter_event_without_a_distinct_id_of_the_user():
    seen = {}

    def opener(request, timeout):
        seen["body"] = json.loads(request.data)
        return FakeResponse()

    posthog_transport("phc_test", opener=opener)([{"event": "consent_outcome", "properties": {"outcome": "share"}}])
    (item,) = seen["body"]["batch"]
    assert item["distinct_id"] == "anonymous-counter"           # one shared constant, never tied to an install


def test_posthog_transport_raises_on_a_server_error_so_the_batch_stays_queued():
    class Bad(FakeResponse):
        status = 500

    send = posthog_transport("phc_test", opener=lambda request, timeout: Bad())
    with pytest.raises(OSError):
        send([{"event": "e", "properties": {}, "install_id": "abc"}])


@pytest.mark.parametrize("host", ["http://us.i.posthog.com", "https://evil.example.com", "https://us.i.posthog.com.evil.example.com"])
def test_posthog_transport_refuses_anything_but_https_on_the_analytics_host(host):
    called = []
    send = posthog_transport("phc_test", host=host, opener=lambda request, timeout: called.append(request) or FakeResponse())
    with pytest.raises(ValueError):
        send([{"event": "e", "properties": {}, "install_id": "abc"}])
    assert called == []


def test_no_project_key_means_no_transport_at_all():
    from mdpipeline.usage import default_transport

    assert default_transport(project_key="") is None


def test_the_shipped_project_key_is_a_public_write_only_phc_key(monkeypatch):
    from mdpipeline.usage import POSTHOG_PROJECT_KEY, default_transport

    monkeypatch.delenv("MDPIPELINE_POSTHOG_KEY", raising=False)
    assert POSTHOG_PROJECT_KEY.startswith("phc_")           # phc_ = project (ingest-only) key; a personal key would start with phx_
    assert default_transport() is not None


def test_the_environment_can_override_or_blank_the_key(monkeypatch):
    from mdpipeline.usage import default_transport

    monkeypatch.setenv("MDPIPELINE_POSTHOG_KEY", "")
    assert default_transport() is None                      # a developer or CI run can switch analytics off entirely


def test_flush_without_a_transport_is_a_silent_no_op():
    store = UsageStore()
    store.set_consent("share")
    store.record("e")
    assert store.flush(None) == 0
    assert store.pending == 1
