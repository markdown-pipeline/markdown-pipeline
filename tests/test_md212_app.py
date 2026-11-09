"""MD-212 (AC7 app, AC8, AC9): the app's state says when background sync isn't running (with the last update in words), the "Start sync again" action
starts it and is counted (notice_acted via app), and the notice is counted once per outage (notice_shown)."""
import pytest

from mdpipeline import autostart, background
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline

from test_background_headless import own, queued
from test_md212_cli_and_events import down  # noqa: F401
from uihelper import running_ui


def state(ui):
    status, data = ui.get("/api/state")
    assert status == 200
    return data["background"]


def test_the_state_says_background_is_not_running_and_when_it_last_updated(down, brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p")
    with running_ui(brain) as ui:
        bg = state(ui)
    assert bg["not_running"] is True and bg["last_update"].startswith("less than a minute ago (")


def test_a_brain_that_never_synced_says_never(down, brain):
    with running_ui(brain) as ui:
        assert state(ui)["last_update"] == "never"


def test_the_state_is_quiet_while_a_process_runs(down, brain, monkeypatch):
    monkeypatch.setattr(background, "is_running", lambda: True)
    with running_ui(brain) as ui:
        bg = state(ui)
    assert bg["not_running"] is False and "last_update" not in bg


def test_start_sync_again_starts_the_process_and_is_counted_as_acted_via_app(down, brain, monkeypatch):
    started = []
    monkeypatch.setattr(autostart, "start_now", lambda **kw: started.append(kw) or True)
    with running_ui(brain) as ui:
        status, _ = ui.post("/api/background", {"enabled": True})
    assert status == 200 and started
    assert [own(e) for e in queued("notice_acted")] == [{"kind": "background_not_running", "via": "app"}]


def test_turning_it_on_when_nothing_was_wrong_is_not_counted(app_data, brain, monkeypatch):
    from mdpipeline.usage import UsageStore
    UsageStore().set_consent("share")
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: True)
    with running_ui(brain) as ui:
        ui.post("/api/background", {"enabled": True})
    assert queued("notice_acted") == []


@pytest.mark.parametrize("surface", ["home", "settings"])
def test_the_page_reports_the_notice_once_per_outage(down, brain, surface):
    with running_ui(brain) as ui:
        for s in (surface, "home", "settings"):
            ui.post("/api/usage/event", {"event": "notice_shown", "properties": {"kind": "background_not_running", "surface": s}})
    assert [own(e) for e in queued("notice_shown")] == [{"kind": "background_not_running", "surface": surface}]
