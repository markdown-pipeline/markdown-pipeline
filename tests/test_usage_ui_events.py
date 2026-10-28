"""MD-116 (FR-S2 AC1): events from the UI (view mode, Activity, snippet copy, errors, opened, credential check) and from the first run."""
from __future__ import annotations

import json

import pytest

import mdpipeline
from mdpipeline import usage, usage_hooks
from mdpipeline.usage import UsageStore
from test_jira_ui import DRAFT_SOURCE, fake  # noqa: F401
from uihelper import running_ui

CANARY = "ACME-secret-title /home/user/private.md ghp_abcdef123456"


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued(*names: str) -> list[dict]:
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def own(e: dict) -> dict:
    return {k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")}


# --- the endpoint the page uses ------------------------------------------------------------------------------------------
def test_the_page_can_report_a_ui_event_and_it_is_queued_cleaned(brain, sharing):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/usage/event", {"event": "view_mode_opened", "properties": {"entry_point": "home", "title": CANARY}})
    assert status == 200 and res == {"queued": True}
    (e,) = queued("view_mode_opened")
    assert own(e) == {"entry_point": "home"} and CANARY not in json.dumps(queued())


@pytest.mark.parametrize("event", ["activity_viewed", "agent_snippet_copied", "source_add_started", "error_shown", "dry_run_viewed"])
def test_every_ui_event_of_the_page_is_accepted(brain, sharing, event):
    props = {"activity_viewed": {"entry_point": "home"}, "agent_snippet_copied": {"agent": "claude_code"},
             "source_add_started": {"source_type": "jira", "entry_point": "editor"}, "error_shown": {"ui_area": "source", "error_class": "auth"},
             "dry_run_viewed": {"items_bucket": "1-10", "would_delete_bucket": "0"}}[event]
    with running_ui(brain) as ui:
        assert ui.post("/api/usage/event", {"event": event, "properties": props})[1] == {"queued": True}
    assert own(queued(event)[0]) == props


@pytest.mark.parametrize("event", ["sync_completed", "agent_read", "first_sync_completed", "made_up", "", None, 5])
def test_an_event_the_ui_does_not_own_is_ignored(brain, sharing, event):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/usage/event", {"event": event, "properties": {"trigger": "manual"}})
    assert status == 200 and res == {"queued": False} and queued() == []


def test_a_bad_body_is_a_plain_400_not_a_crash(brain, sharing):
    with running_ui(brain) as ui:
        status, _ = ui.request("POST", "/api/usage/event", body=[1, 2])
    assert status in (200, 400)
    assert queued() == []


def test_without_consent_the_endpoint_stores_nothing(brain):
    with running_ui(brain) as ui:
        assert ui.post("/api/usage/event", {"event": "view_mode_opened", "properties": {"entry_point": "home"}})[1] == {"queued": False}
    assert UsageStore().pending == 0


def test_the_endpoint_needs_the_session_token(brain, sharing):
    with running_ui(brain) as ui:
        assert ui.post("/api/usage/event", {"event": "view_mode_opened"}, token=False)[0] == 403
    assert queued() == []


# --- opened / updated ----------------------------------------------------------------------------------------------------
def test_opening_the_page_reports_app_opened_once_per_server_with_first_open_true_only_the_first_time(brain, sharing):
    with running_ui(brain) as ui:
        ui.get("/")
        ui.get("/")
    with running_ui(brain) as ui:
        ui.get("/")
    opened = queued("app_opened")
    assert [own(e) for e in opened] == [{"first_open": True}, {"first_open": False}]


def test_the_first_open_starts_the_clock_even_without_consent(brain):
    with running_ui(brain) as ui:
        ui.get("/")
    assert UsageStore().ms_since_first_open() is not None and UsageStore().pending == 0


def test_a_new_version_reports_app_updated_with_the_previous_one_only(brain, sharing, monkeypatch):
    before = mdpipeline.__version__
    usage_hooks.app_opened()                                          # remembers the current version
    monkeypatch.setattr(mdpipeline, "__version__", "0.2.0")
    monkeypatch.setattr(usage, "__version__", "0.2.0")
    usage_hooks.app_opened()
    (updated,) = queued("app_updated")
    assert own(updated) == {"previous_version": before} and updated["properties"]["app_version"] == "0.2.0"
    usage_hooks.app_opened()
    assert len(queued("app_updated")) == 1


# --- consent decided -------------------------------------------------------------------------------------------------------
def test_choosing_share_reports_consent_decided_and_declining_reports_nothing(brain):
    with running_ui(brain) as ui:
        ui.post("/api/usage/consent", {"choice": "decline"})
        assert UsageStore().pending == 0
        ui.post("/api/usage/consent", {"choice": "share"})
    assert [own(e) for e in queued("consent_decided")] == [{"choice": "share"}]


def test_undoing_a_share_drops_the_queue_including_the_consent_event(brain):
    with running_ui(brain) as ui:
        ui.post("/api/usage/consent", {"choice": "share"})
        ui.post("/api/usage/consent", {"choice": "unanswered"})
    assert UsageStore().pending == 0


# --- the Jira credential check (UI) ------------------------------------------------------------------------------------------
def test_a_successful_credential_check_reports_started_then_completed(brain, fake, sharing):
    with running_ui(brain) as ui:
        ui.post("/api/jira-test", DRAFT_SOURCE)
    assert [(e["event"], own(e)) for e in queued("source_auth_started", "source_auth_completed", "source_auth_failed")] == [
        ("source_auth_started", {"source_type": "jira", "auth_method": "token"}),
        ("source_auth_completed", {"source_type": "jira", "auth_method": "token"})]


@pytest.mark.parametrize("override,error_class", [({"token_ref": "env:NOT_SET_ANYWHERE"}, "auth"), ({"jql": "project = = ="}, "validation"),
                                                  ({"site": "https://evil.example.com"}, "validation")])
def test_a_failed_credential_check_reports_started_then_failed_with_an_error_class_and_never_the_message(brain, fake, sharing, override, error_class):
    with running_ui(brain) as ui:
        ui.post("/api/jira-test", {**DRAFT_SOURCE, **override})
    events = queued("source_auth_started", "source_auth_completed", "source_auth_failed")
    assert [e["event"] for e in events] == ["source_auth_started", "source_auth_failed"]
    assert own(events[1])["error_class"] == error_class
    text = json.dumps(queued())
    assert "evil.example.com" not in text and "NOT_SET_ANYWHERE" not in text and "project = = =" not in text and "acme" not in text.replace("acme-", "")
