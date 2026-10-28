"""MD-154: our own installs are tagged internal so the first self-serve funnel reads real users only. Default false; set by MDPIPELINE_INTERNAL=1; kept."""
import json

import pytest

from mdpipeline import cli, install_info, usage, usage_hooks
from mdpipeline.usage import UsageStore


def events(name=None):
    data = json.loads((usage.data_dir() / "usage.json").read_text(encoding="utf-8"))
    return [e for e in data.get("queue", []) if name is None or e["event"] == name]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MDPIPELINE_INTERNAL", raising=False)       # the test suite itself runs marked internal (AC3); these tests start from a real user's state
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "script")


def share():
    UsageStore().decide("share")


def test_by_default_every_event_says_it_is_not_internal(app_data):
    share()
    usage.emit("try_started")
    assert events() and all(e["properties"]["is_internal"] is False for e in events())


def test_the_environment_variable_marks_every_event_internal(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    share()
    usage.emit("try_started")
    assert events() and all(e["properties"]["is_internal"] is True for e in events())


def test_the_mark_is_kept_after_a_restart_and_an_upgrade_without_the_variable(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    share()
    usage.emit("try_started")
    monkeypatch.delenv("MDPIPELINE_INTERNAL")
    usage.new_session()                                            # a restart: new session, no variable
    monkeypatch.setattr("mdpipeline.usage.__version__", "9.9.9")   # an upgrade: a new version of the same install
    usage.emit("try_started")
    last = events("try_started")[-1]["properties"]
    assert last["is_internal"] is True and last["app_version"] == "9.9.9"


def test_setting_the_variable_to_zero_takes_the_mark_off(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    share()
    usage.emit("try_started")
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "0")
    usage.emit("try_started")
    assert events("try_started")[-1]["properties"]["is_internal"] is False
    monkeypatch.delenv("MDPIPELINE_INTERNAL")
    usage.emit("try_started")
    assert events("try_started")[-1]["properties"]["is_internal"] is False


def test_the_install_script_can_mark_the_install_before_anyone_has_chosen_anything(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    assert cli.main(["install-note", "script"]) == 0
    monkeypatch.delenv("MDPIPELINE_INTERNAL")
    share()
    usage.emit("try_started")
    assert events("try_started")[-1]["properties"]["is_internal"] is True


def test_install_completed_from_a_public_install_carries_the_version_the_os_and_the_internal_flag(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    share()
    (event,) = events("install_completed")
    p = event["properties"]
    assert p["install_method"] == "script" and p["os"] == install_info.os_name()
    assert p["app_version"] and p["is_internal"] is True


def test_the_flag_changes_nothing_about_consent_nothing_is_recorded_before_the_person_agrees(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "1")
    assert usage.emit("try_started") is False
    usage_hooks.app_opened()
    assert events() == [] and UsageStore().consent == "unanswered"


def test_the_flag_is_a_plain_true_or_false_never_text(app_data, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "owner@example.com")      # anything that is not a yes/no word is not "internal"
    share()
    usage.emit("try_started")
    assert events("try_started")[-1]["properties"]["is_internal"] is False
