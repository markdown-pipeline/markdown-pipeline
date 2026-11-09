"""MD-212 (AC7 CLI text, AC8, AC9): the CLI says plainly that background sync isn't running (consequence first, one next step), counts the notice once per
outage (notice_shown), counts the restart done while it was up (notice_acted, via cli), and the notice goes away by itself when a process ticks again."""
import re

import pytest

from mdpipeline import autostart, background, cli, status
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.schedulestore import SchedulerStore
from mdpipeline.usage import UsageStore

from test_background_headless import own, queued
from test_md212_not_running import RESTART, set_state

NOW = 1_800_000_000.0


@pytest.fixture
def down(monkeypatch, app_data):
    """Background is on and announced, nothing runs, and a command cannot start it (so the notice is what the person sees)."""
    UsageStore().set_consent("share")
    set_state(last_tick_at=None)
    monkeypatch.setattr(background, "is_running", lambda: False)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: False)
    monkeypatch.setattr(autostart, "ensure_item", lambda **kw: False)


# --- the words ------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seconds, relative", [(30, "less than a minute ago"), (5 * 60, "5 minutes ago"), (3 * 3600, "3 hours ago"), (2 * 86400, "2 days ago")])
def test_the_last_update_is_a_relative_time_and_a_local_date_and_time(seconds, relative):
    phrase = status.last_update_phrase(NOW - seconds, NOW)
    assert phrase.startswith(relative) and re.search(r"\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)$", phrase)


def test_a_never_synced_brain_says_never():
    assert status.last_update_phrase(None, NOW) == "never"


def test_the_cli_text_is_consequence_first_with_the_restart_command():
    text = status.background_down_cli_text(NOW - 3 * 3600, NOW)
    assert text.startswith("Your files may be out of date: background sync isn't running. Last update: 3 hours ago (")
    assert text.endswith(f"To start it again: {RESTART}")


def test_the_cli_text_when_nothing_has_synced_yet():
    assert status.background_down_cli_text(None, NOW) == f"Background sync isn't running, and nothing has synced yet. To start it: {RESTART}"


# --- the CLI ----------------------------------------------------------------------------------------------------------------------------------
def test_status_prints_the_notice_when_background_is_down(down, brain, add_pipeline, make_source, capsys):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p")
    cli.main(["--brain", str(brain.root), "status"])
    out = capsys.readouterr().out
    assert "Your files may be out of date: background sync isn't running." in out and RESTART in out


def test_status_prints_no_notice_while_background_runs(down, monkeypatch, brain, capsys):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: __import__("mdpipeline").__version__)
    cli.main(["--brain", str(brain.root), "status"])
    assert "may be out of date" not in capsys.readouterr().out


def test_schedule_show_says_the_same_when_it_is_down(down, brain, add_pipeline, make_source, capsys):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p")
    cli.main(["--brain", str(brain.root), "schedule"])
    assert "Your files may be out of date: background sync isn't running." in capsys.readouterr().out


# --- usage events -------------------------------------------------------------------------------------------------------------------------------
def test_the_notice_is_counted_once_per_outage_whatever_the_number_of_commands(down, brain, capsys):
    for _ in range(3):
        cli.main(["--brain", str(brain.root), "status"])
    assert [own(e) for e in queued("notice_shown")] == [{"kind": "background_not_running", "surface": "cli"}]


def test_a_new_outage_after_a_heartbeat_is_counted_again(down, brain, capsys):
    cli.main(["--brain", str(brain.root), "status"])
    background.record_heartbeat(NOW)                       # AC8: a process ticked: the notice is gone and the next outage is a new occurrence
    set_state(last_tick_at=None)
    cli.main(["--brain", str(brain.root), "status"])
    assert len(queued("notice_shown")) == 2


def test_the_restart_command_while_the_notice_is_up_is_counted_as_acted(down, brain, monkeypatch, capsys):
    monkeypatch.setattr(autostart, "start_now", lambda **kw: True)
    cli.main(["--brain", str(brain.root), "schedule", "background", "restart"])
    assert [own(e) for e in queued("notice_acted")] == [{"kind": "background_not_running", "via": "cli"}]


def test_a_restart_when_nothing_was_wrong_is_not_counted_as_acted(app_data, brain, monkeypatch, capsys):
    UsageStore().set_consent("share")
    set_state(last_tick_at=NOW)
    monkeypatch.setattr(background, "not_running", lambda now=None: False)
    monkeypatch.setattr(background, "restart", lambda: "restarting")
    cli.main(["--brain", str(brain.root), "schedule", "background", "restart"])
    assert queued("notice_acted") == []


# --- AC8 ---------------------------------------------------------------------------------------------------------------------------------------
def test_the_notice_disappears_by_itself_once_a_process_ticks_again(down, brain, capsys):
    cli.main(["--brain", str(brain.root), "status"])
    assert "sync isn't running" in capsys.readouterr().out
    background.record_heartbeat()                           # the process is back
    cli.main(["--brain", str(brain.root), "status"])
    assert "sync isn't running" not in capsys.readouterr().out
