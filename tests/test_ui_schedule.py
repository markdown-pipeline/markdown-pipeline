"""MD-121 AC4/AC5/AC7: the UI API behind Home's last/next sync, the "Needs you" notices and the background-process settings."""
import time

import pytest

from mdpipeline import autostart, notices
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from uihelper import running_ui


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def one(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    return brain


def test_a_home_row_carries_the_interval_and_last_and_next_sync(one):
    sync_pipeline(one, "p", trigger="cli")
    with running_ui(one) as ui:
        (row,) = ui.get("/api/state")[1]["pipelines"]
    assert row["schedule"] == "1h" and row["last_sync"] and row["next_sync"] > time.time()


def test_a_pipeline_that_never_synced_has_no_next_sync(one):
    with running_ui(one) as ui:
        (row,) = ui.get("/api/state")[1]["pipelines"]
    assert row["next_sync"] is None and row["last_sync"] is None


def test_the_interval_is_changed_with_a_post(one):
    with running_ui(one) as ui:
        status, view = ui.post("/api/pipelines/p/schedule", {"interval": "6h"})
        assert ui.get("/api/state")[1]["pipelines"][0]["schedule"] == "6h"
    assert status == 200 and view == {"name": "p", "schedule": "6h", "changed": True}
    assert load_config(one).get("p").schedule == "6h"


def test_a_bad_interval_is_a_400_with_the_choices(one):
    with running_ui(one) as ui:
        status, view = ui.post("/api/pipelines/p/schedule", {"interval": "weekly"})
    assert status == 400 and "15m" in str(view)


def test_an_unknown_pipeline_is_a_404(one):
    with running_ui(one) as ui:
        assert ui.post("/api/pipelines/nope/schedule", {"interval": "6h"})[0] == 404


def test_needs_you_notices_are_in_the_state_at_the_top_of_home(one):
    sync_pipeline(one, "p", trigger="cli")
    for _ in range(2):
        ActivityLog(one).append({"type": "run", "pipeline": "p", "trigger": "schedule", "result": "error", "counts": {}, "conflicts": [], "errors": [{"source": "a", "error": "x", "category": "network"}]})
    notices.check(one)
    with running_ui(one) as ui:
        status, view = ui.get("/api/state")
        assert status == 200, (status, view)
        got = view["notices"]
    assert [(n["pipeline"], n["kind"]) for n in got] == [("p", "sync_failed")]
    assert "x" not in str(got)


def test_state_has_no_notices_when_all_is_well(one):
    sync_pipeline(one, "p", trigger="cli")
    with running_ui(one) as ui:
        assert ui.get("/api/state")[1]["notices"] == []


def test_the_state_describes_the_background_process(one):
    with running_ui(one) as ui:
        bg = ui.get("/api/state")[1]["background"]
    assert bg == {"enabled": True, "announced": False, "running": False, "announce_pending": False, "linger_hint": False, "linger_notice": False}


def test_the_first_ui_sync_turns_the_background_on_and_returns_the_plain_words_once(one):
    with running_ui(one) as ui:
        first = ui.post("/api/pipelines/p/sync", {})[1]
        second = ui.post("/api/pipelines/p/sync", {})[1]
    assert "keeps running when this window is closed" in first["announcement"]
    assert "announcement" not in second


def test_sync_all_also_announces_once(one):
    with running_ui(one) as ui:
        assert "starts at login" in ui.post("/api/sync-all", {})[1]["announcement"]


def test_turning_the_background_off_and_on_from_settings(one):
    with running_ui(one) as ui:
        assert ui.post("/api/background", {"enabled": False})[1]["enabled"] is False
        assert ui.get("/api/state")[1]["background"]["enabled"] is False
        assert ui.post("/api/background", {"enabled": True})[1]["enabled"] is True


def test_a_background_post_needs_a_boolean(one):
    with running_ui(one) as ui:
        assert ui.post("/api/background", {"enabled": "maybe"})[0] == 400


def test_editing_a_pipeline_keeps_its_chosen_interval(one, make_source):
    """The editor rebuilds the pipeline from the form: the interval is not a form field and must survive."""
    from mdpipeline.service import set_schedule

    set_schedule(one, "p", "6h", trigger="cli")
    src = load_config(one).get("p").sources[0].path
    draft = {"name": "p", "sources": [{"path": src, "label": "a"}], "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"],
             "exclude": [".git/**"], "on_source_delete": "keep", "guard_threshold_percent": 40, "guard_min_files": 5}
    with running_ui(one) as ui:
        status, _ = ui.put("/api/pipelines/p", {"draft": draft})
    assert status == 200 and load_config(one).get("p").on_source_delete == "keep"
    assert load_config(one).get("p").schedule == "6h"


def test_view_mode_status_shows_the_interval_and_last_and_next_sync(one):
    sync_pipeline(one, "p", trigger="cli")
    with running_ui(one) as ui:
        status, view = ui.get("/api/pipelines/p/view")
    st = view["status"]
    assert st["schedule"] == "1h" and st["last_sync"] and st["next_sync"] > time.time()


def test_the_announcement_survives_a_page_reload_until_it_is_acknowledged(one):
    with running_ui(one) as ui:
        ui.post("/api/pipelines/p/sync", {})
        bg = ui.get("/api/state")[1]["background"]
        assert bg["announce_pending"] is True and "starts at login" in bg["announcement"]
        again = ui.post("/api/pipelines/p/sync", {})[1]
        assert "announcement" not in again                               # the response says it once; the state keeps it
        assert ui.get("/api/state")[1]["background"]["announce_pending"] is True
        assert ui.post("/api/background/ack", {})[0] == 200
        assert ui.get("/api/state")[1]["background"]["announce_pending"] is False


def test_turning_off_from_the_banner_acknowledges_it(one):
    with running_ui(one) as ui:
        ui.post("/api/pipelines/p/sync", {})
        ui.post("/api/background", {"enabled": False})
        assert ui.get("/api/state")[1]["background"]["announce_pending"] is False
