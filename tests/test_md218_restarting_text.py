"""MD-218: right after `mdpipeline update` (or `schedule background restart`) the old process is still finishing its sync. Status then says the restart is
UNDER WAY and never asks the person to do what is already happening. If the old version is still running after the window, the "Restart it" line is
shown, unchanged. The window is anchored to the FIRST request (a later command's own self-heal does not extend it)."""
import json

import pytest

import mdpipeline
from mdpipeline import autostart, background
from mdpipeline.cli import main

from test_background_restart import run_cli, _brain_for_cli  # noqa: F401

NOW = 1_800_000_000.0
OLD, NEW = "0.1.17", "0.1.18"


@pytest.fixture
def old_process(monkeypatch):
    """An installed 0.1.18 with a 0.1.17 background process still running."""
    monkeypatch.setattr(mdpipeline, "__version__", NEW)
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: OLD)
    monkeypatch.setattr(background, "restart_if_outdated", lambda: False)      # these tests read the status; the self-heal has its own test below


def request_restart(at, **kw):
    background.mark_restart_requested(at, **kw)


# --- the window ------------------------------------------------------------------------------------------------------------------------------
def test_there_is_no_pending_restart_before_one_was_requested(app_data, old_process):
    assert background.restart_pending(NOW) is False


def test_a_restart_is_pending_inside_the_window_while_the_old_version_runs(app_data, old_process):
    request_restart(NOW)
    assert background.restart_pending(NOW + background.RESTART_PENDING_SECONDS - 1) is True


def test_it_is_not_pending_after_the_window(app_data, old_process):
    request_restart(NOW)
    assert background.restart_pending(NOW + background.RESTART_PENDING_SECONDS + 1) is False


def test_the_window_is_at_least_60_seconds(app_data):
    assert background.RESTART_PENDING_SECONDS >= 60


def test_it_is_not_pending_once_the_running_process_is_the_installed_version(app_data, old_process, monkeypatch):
    request_restart(NOW)
    monkeypatch.setattr(background, "running_version", lambda: NEW)
    assert background.restart_pending(NOW + 1) is False


def test_the_first_request_anchors_the_window(app_data, old_process):
    request_restart(NOW)
    request_restart(NOW + 50, only_if_none=True)           # a later command's self-heal
    assert background.restart_pending(NOW + background.RESTART_PENDING_SECONDS + 1) is False


def test_an_explicit_restart_command_starts_a_new_window(app_data, old_process):
    request_restart(NOW)
    request_restart(NOW + 500)
    assert background.restart_pending(NOW + 501) is True


def test_a_new_background_process_clears_the_request(app_data, old_process, monkeypatch):
    from mdpipeline import scheduler
    request_restart(NOW)
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    background.run(max_ticks=1, sleep=lambda s: None, interval=1, how="manual")
    assert not background._restart_marker_path().exists()


def test_restart_writes_the_request_when_it_asks_the_old_process_to_stop(app_data, old_process, monkeypatch):
    monkeypatch.setattr(background, "stop", lambda: True)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: True)
    assert background.restart() == "restarting"          # 0.1.17 already finishes its sync when asked to stop
    assert background._restart_marker_path().exists()


# --- the words -------------------------------------------------------------------------------------------------------------------------------
RESTARTING = f"Background sync: on, restarting on version {NEW}. A sync in progress finishes first."
RESTART_IT = f"Background sync: on, but it is still running the older version {OLD} (installed: {NEW}). Restart it: mdpipeline schedule background restart"


def test_schedule_inside_the_window_says_restarting_and_does_not_ask_for_a_restart(app_data, old_process, capsys):
    request_restart(__import__("time").time())
    _, out, _ = run_cli(capsys, "schedule", "show")
    assert RESTARTING in out and "Restart it" not in out


def test_schedule_after_the_window_shows_the_restart_it_line_unchanged(app_data, old_process, capsys):
    request_restart(__import__("time").time() - background.RESTART_PENDING_SECONDS - 5)
    _, out, _ = run_cli(capsys, "schedule", "show")
    assert RESTART_IT in out and "restarting on version" not in out


def test_schedule_without_any_request_shows_the_restart_it_line_unchanged(app_data, old_process, capsys):
    _, out, _ = run_cli(capsys, "schedule", "show")
    assert RESTART_IT in out


def test_the_app_state_says_restarting_inside_the_window_only(app_data, old_process, brain):
    from uihelper import running_ui
    request_restart(__import__("time").time())
    with running_ui(brain) as ui:
        inside = ui.get("/api/state")[1]["background"]
    request_restart(__import__("time").time() - background.RESTART_PENDING_SECONDS - 5)
    with running_ui(brain) as ui:
        after = ui.get("/api/state")[1]["background"]
    assert inside["restarting"] is True and after["restarting"] is False and after["outdated"] is True
