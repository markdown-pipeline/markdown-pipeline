"""MD-121: `mdpipeline schedule ...` and `mdpipeline background`."""
import pytest

from mdpipeline import autostart, background, cli, scheduler
from mdpipeline.cli import main, needs_usage_notice, starts_usage_helper
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline


@pytest.fixture
def synced(brain, add_pipeline, make_source, monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    return brain


def run(brain, *argv):
    return main(["--brain", str(brain.root), *argv])


def test_schedule_shows_each_pipeline_with_its_interval_last_and_next_sync(synced, capsys):
    assert run(synced, "schedule") == 0
    out = capsys.readouterr().out
    assert "p: every 1h; last sync" in out and "next sync in" in out and "Background sync:" in out


def test_schedule_says_when_a_pipeline_has_not_synced_yet(brain, add_pipeline, make_source, capsys):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    run(brain, "schedule")
    assert "after its first sync" in capsys.readouterr().out


def test_schedule_set_changes_the_interval(synced, capsys):
    assert run(synced, "schedule", "set", "p", "6h") == 0
    assert load_config(synced).get("p").schedule == "6h" and "6h" in capsys.readouterr().out


def test_schedule_set_with_a_bad_interval_is_a_usage_error_and_lists_the_choices(synced, capsys):
    assert run(synced, "schedule", "set", "p", "weekly") == cli.EXIT_USAGE
    assert "15m" in capsys.readouterr().err and load_config(synced).get("p").schedule == "1h"


def test_schedule_set_of_an_unknown_pipeline_is_a_usage_error(synced, capsys):
    assert run(synced, "schedule", "set", "nope", "6h") == cli.EXIT_USAGE


def test_schedule_background_off_and_on(synced, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(autostart, "disable", lambda **kw: calls.append("disable") or True)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: calls.append("start"))
    assert run(synced, "schedule", "background", "off") == 0
    assert scheduler.background_enabled() is False and calls == ["disable"]
    assert run(synced, "schedule", "background", "on") == 0
    assert scheduler.background_enabled() is True and "start" in calls


def test_background_runs_the_loop(app_data, monkeypatch):
    called = []
    monkeypatch.setattr(background, "run", lambda **kw: called.append(kw) or True)
    assert main(["background"]) == 0 and called == [{"how": "manual"}]


def test_background_exits_quietly_when_one_is_already_running(app_data, monkeypatch, capsys):
    monkeypatch.setattr(background, "run", lambda **kw: False)
    assert main(["background"]) == 0
    assert "already running" in capsys.readouterr().out


def test_background_prints_no_first_run_notice_and_starts_no_usage_helper():
    assert needs_usage_notice("background") is False and starts_usage_helper("background") is False
