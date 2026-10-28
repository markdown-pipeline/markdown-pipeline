"""MD-117 AC6: install_completed goes out once, at first launch, after the person agreed to share (never before, never for a decline)."""
import json

import pytest

from mdpipeline import install_info, usage, usage_hooks
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import INSTALL_METHOD, OS_NAME


def queued():
    data = json.loads((usage.data_dir() / "usage.json").read_text(encoding="utf-8"))
    return [e for e in data.get("queue", []) if e["event"] == "install_completed"]


@pytest.fixture(autouse=True)
def method(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "script")


def test_nothing_before_the_person_has_chosen(app_data):
    usage_hooks.app_opened()
    assert queued() == []


def test_sharing_sends_install_completed_with_the_os_and_the_install_method(app_data):
    UsageStore().decide("share")
    (event,) = queued()
    assert event["properties"]["os"] == install_info.os_name() and event["properties"]["os"] in OS_NAME.values
    assert event["properties"]["install_method"] == "script"


def test_declining_sends_nothing_now_or_later(app_data):
    UsageStore().decide("decline")
    usage_hooks.app_opened()
    assert queued() == []
    assert UsageStore().pending == 0


def test_it_is_sent_once_even_if_the_choice_is_made_again(app_data):
    store = UsageStore()
    store.decide("share")
    store.decide("share")
    assert len(queued()) == 1
    store.set_consent("unanswered")                 # undo drops what waited ...
    store.decide("share")                           # ... and sharing again does not announce a second install
    assert queued() == []


def test_an_install_that_already_shared_before_this_feature_does_not_report_an_install_on_update(app_data):
    UsageStore().set_consent("share")                 # shared earlier, through another path
    usage_hooks.app_opened()
    assert queued() == []


def test_the_event_carries_only_the_two_fixed_properties(app_data):
    UsageStore().decide("share")
    (event,) = queued()
    assert set(event["properties"]) - {"app_version", "deployment", "session_id", "is_internal"} == {"os", "install_method"}
