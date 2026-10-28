"""MD-121 AC1/AC5/AC6/AC7/AC8: change an interval, last/next sync in status, the first-turn-on announcement."""
import time

import pytest

from mdpipeline import autostart, notices, scheduler, usage
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.service import NotFound, set_schedule
from mdpipeline.status import brain_status

HOUR = 3600


@pytest.fixture
def events(monkeypatch):
    out = []
    monkeypatch.setattr(usage, "emit", lambda name, **p: out.append((name, p)))
    return out


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    return brain


# --- AC1/AC6: changing the interval ---------------------------------------------------------------------------------
def test_the_interval_can_be_changed_per_pipeline_and_is_saved(synced, events):
    assert set_schedule(synced, "p", "6h", trigger="cli") is True
    assert load_config(synced).get("p").schedule == "6h"


def test_the_change_emits_schedule_changed_with_the_old_and_the_new_interval(synced, events):
    set_schedule(synced, "p", "daily", trigger="ui")
    assert ("schedule_changed", {"old_interval": "1h", "new_interval": "daily"}) in events


def test_setting_the_same_interval_changes_nothing_and_emits_nothing(synced, events):
    assert set_schedule(synced, "p", "1h", trigger="cli") is False
    assert [e for e in events if e[0] == "schedule_changed"] == []


def test_an_unknown_interval_is_refused_and_nothing_is_saved(synced):
    with pytest.raises(ValueError, match="15m"):
        set_schedule(synced, "p", "every 5 minutes", trigger="cli")
    assert load_config(synced).get("p").schedule == "1h"


def test_an_unknown_pipeline_is_not_found(synced):
    with pytest.raises(NotFound):
        set_schedule(synced, "nope", "6h", trigger="cli")


def test_turning_a_pipeline_off_is_a_change_like_any_other(synced, events):
    set_schedule(synced, "p", "off", trigger="cli")
    assert ("schedule_changed", {"old_interval": "1h", "new_interval": "off"}) in events


# --- AC5: last sync / next sync ---------------------------------------------------------------------------------------
def test_status_shows_when_a_pipeline_synced_last_and_when_it_syncs_next(synced):
    now = time.time()
    (p,) = brain_status(synced, now=now)["pipelines"]
    assert p["schedule"] == "1h"
    assert p["next_sync"] == pytest.approx(now + HOUR, abs=30) and p["last_sync"] == p["last_run"]["ts"]


def test_status_has_no_next_sync_before_the_first_sync(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    (p,) = brain_status(brain)["pipelines"]
    assert p["next_sync"] is None and p["last_sync"] is None


def test_status_has_no_next_sync_when_off_or_paused(synced):
    set_schedule(synced, "p", "off", trigger="cli")
    assert brain_status(synced)["pipelines"][0]["next_sync"] is None


def test_status_next_sync_of_an_overdue_pipeline_is_now(synced):
    later = time.time() + 30 * HOUR
    assert brain_status(synced, now=later)["pipelines"][0]["next_sync"] == later


# --- AC7/AC8: turning the background process on, in plain words, once ------------------------------------------------
@pytest.fixture
def login_item(monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "enable", lambda **kw: calls.append("enable"))
    monkeypatch.setattr(autostart, "start_now", lambda **kw: calls.append("start"))
    monkeypatch.setattr(autostart, "disable", lambda **kw: calls.append("disable") or True)
    return calls


def test_the_first_turn_on_installs_the_login_item_starts_it_and_says_so_in_plain_words(app_data, login_item):
    text = scheduler.ensure_background()
    assert login_item == ["enable", "start"]
    assert "keeps running when this window is closed" in text and "starts at login" in text and "turn it off any time" in text


def test_the_announcement_is_made_only_once(app_data, login_item):
    scheduler.ensure_background()
    assert scheduler.ensure_background() is None and login_item == ["enable", "start"]


def test_an_opt_out_is_respected_nothing_is_installed_and_nothing_announced(app_data, login_item):
    scheduler.set_background(False)
    assert scheduler.ensure_background() is None and login_item == []


def test_turning_it_off_removes_the_login_item_and_turning_it_on_again_restores_it(app_data, login_item):
    scheduler.ensure_background()
    scheduler.turn_off_background()
    assert scheduler.background_enabled() is False and login_item[-1] == "disable"
    scheduler.turn_on_background()
    assert scheduler.background_enabled() is True and login_item[-2:] == ["enable", "start"]


def test_when_the_platform_has_no_login_item_nothing_is_announced(app_data, monkeypatch):
    def refuse(**kw):
        raise autostart.AutostartError("no")

    monkeypatch.setattr(autostart, "enable", refuse)
    assert scheduler.ensure_background() is None


def test_the_first_sync_turns_it_on_the_cli_shows_the_announcement(brain, add_pipeline, make_source, login_item, capsys):
    from mdpipeline.cli import main

    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    assert main(["--brain", str(brain.root), "sync", "p"]) == 0
    out = capsys.readouterr().out
    assert "starts at login" in out and login_item == ["enable", "start"]
    main(["--brain", str(brain.root), "sync", "p"])
    assert "starts at login" not in capsys.readouterr().out


def test_a_scheduled_run_never_announces_or_installs_anything(synced, login_item):
    scheduler.run_due(synced, now=time.time() + 2 * HOUR)
    assert login_item == []


# --- review fixes: the words stay until the person has seen them (F1), not-running signal (N1) ----------------------
def test_the_announcement_stays_pending_until_it_is_acknowledged(app_data, login_item):
    from mdpipeline.uiapi import background_view

    scheduler.ensure_background()
    assert background_view()["announce_pending"] is True and background_view()["announcement"] == scheduler.ANNOUNCEMENT
    scheduler.acknowledge_announcement()
    assert background_view()["announce_pending"] is False and "announcement" not in background_view()


def test_a_terminal_that_printed_the_words_counts_as_acknowledged(app_data, login_item):
    from mdpipeline.uiapi import background_view

    scheduler.ensure_background(acknowledge=True)
    assert background_view()["announce_pending"] is False


def test_turning_it_off_or_on_counts_as_having_seen_the_words(app_data, login_item):
    from mdpipeline.uiapi import background_view

    scheduler.ensure_background()
    scheduler.turn_off_background()
    assert background_view()["announce_pending"] is False


def test_the_cli_sync_acknowledges_what_it_printed(brain, add_pipeline, make_source, login_item, capsys):
    from mdpipeline.cli import main
    from mdpipeline.uiapi import background_view

    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    main(["--brain", str(brain.root), "sync", "p"])
    assert "starts at login" in capsys.readouterr().out and background_view()["announce_pending"] is False


def test_the_cli_says_when_background_sync_is_on_but_not_running(synced, capsys):
    from mdpipeline.cli import main

    main(["--brain", str(synced.root), "schedule"])
    out = capsys.readouterr().out
    assert "on but not running" in out and "mdpipeline background" in out
