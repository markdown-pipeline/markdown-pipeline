"""MD-115 (FR-S1 AC1, AC3, AC5): the UI API behind the consent card and Settings → Privacy & usage."""
from __future__ import annotations

import pytest

from mdpipeline import usage
from mdpipeline.usage import UsageStore
from uihelper import running_ui


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


def test_a_new_install_has_no_answer_and_shows_the_lists_and_an_empty_log(brain):
    with running_ui(brain) as ui:
        status, view = ui.get("/api/usage")
    assert status == 200
    assert view["consent"] == "unanswered" and view["sharing"] is False
    assert view["what_we_send"] == list(usage.WHAT_WE_SEND) and view["never_sent"] == list(usage.NEVER_SENT)
    assert view["log"] == [] and view["install_id"]


def test_the_home_state_carries_the_consent_so_the_card_knows_whether_to_show(brain):
    with running_ui(brain) as ui:
        assert ui.get("/api/state")[1]["usage"] == {"consent": "unanswered", "sharing": False}
        ui.post("/api/usage/consent", {"choice": "decline"})
        assert ui.get("/api/state")[1]["usage"] == {"consent": "decline", "sharing": False}


@pytest.mark.parametrize("choice,consent,sharing", [("share", "share", True), ("decline", "decline", False), ("no_answer", "unanswered", False)])
def test_choosing_is_stored_and_effective_at_once(brain, choice, consent, sharing):
    with running_ui(brain) as ui:
        status, view = ui.post("/api/usage/consent", {"choice": choice})
        again = ui.get("/api/usage")[1]
    assert status == 200 and view["consent"] == consent and view["sharing"] is sharing
    assert again["consent"] == consent and UsageStore().consent == consent


def test_undo_returns_to_not_chosen_without_counting_anything(brain, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().set_counter_enabled(True)
    with running_ui(brain) as ui:
        ui.post("/api/usage/consent", {"choice": "share"})
        status, view = ui.post("/api/usage/consent", {"choice": "unanswered"})
    assert status == 200 and view["consent"] == "unanswered" and view["sharing"] is False
    assert [e["properties"]["outcome"] for e in transport.events] == ["share"]       # only the first decision was counted; undo adds nothing


def test_an_unknown_choice_is_refused_and_nothing_changes(brain):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/usage/consent", {"choice": "maybe"})
    assert status == 400 and "error" in res and UsageStore().consent == "unanswered"


def test_a_missing_choice_is_refused(brain):
    with running_ui(brain) as ui:
        status, _ = ui.post("/api/usage/consent", {})
    assert status == 400


def test_switching_to_decline_drops_what_waited_to_be_sent(brain):
    store = UsageStore()
    store.set_consent("share")
    store.record("e")
    with running_ui(brain) as ui:
        ui.post("/api/usage/consent", {"choice": "decline"})
    assert UsageStore().pending == 0


def test_the_log_is_shown_newest_first_with_what_the_user_chose_to_send_marked(brain):
    store = UsageStore()
    store.set_consent("share")
    store.record("first_sync_completed", {"source_type": "jira"})
    store.flush(Recorder())
    store.send_user_chosen("source_requested", {"source_id": "notion"}, Recorder())
    with running_ui(brain) as ui:
        log = ui.get("/api/usage")[1]["log"]
    assert [(e["event"], e["chosen"]) for e in log] == [("source_requested", True), ("first_sync_completed", False)]


def test_reset_gives_a_new_install_id(brain):
    old = UsageStore().install_id
    with running_ui(brain) as ui:
        status, view = ui.post("/api/usage/reset-id")
    assert status == 200 and view["install_id"] != old and UsageStore().install_id == view["install_id"]


def test_the_first_decision_is_counted_when_the_counter_is_on(brain, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().set_counter_enabled(True)
    with running_ui(brain) as ui:
        ui.post("/api/usage/consent", {"choice": "no_answer"})
        ui.post("/api/usage/consent", {"choice": "share"})
    assert [e["properties"]["outcome"] for e in transport.events] == ["no_answer"]


def test_the_view_never_shows_the_counter_switch_or_the_queue(brain):
    with running_ui(brain) as ui:
        view = ui.get("/api/usage")[1]
    assert "counter_enabled" not in view and "queue" not in view


def test_changes_need_the_session_token(brain):
    with running_ui(brain) as ui:
        assert ui.post("/api/usage/consent", {"choice": "share"}, token=False)[0] == 403
        assert ui.post("/api/usage/reset-id", token=False)[0] == 403
    assert UsageStore().consent == "unanswered"


# --- MD-197 (marketing MM-147): the Settings text names the command the person can actually run --------------------------------------
@pytest.mark.parametrize("method,expected", [("uvx", "uvx mdpipeline"), ("registry", "uvx mdpipeline"), ("uv", "mdpipeline")])
def test_the_usage_view_carries_the_command_for_the_install_method(brain, monkeypatch, method, expected):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", method)
    with running_ui(brain) as ui:
        assert ui.get("/api/usage")[1]["command"] == expected
