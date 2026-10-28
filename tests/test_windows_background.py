"""MD-181 (W3): background sync on Windows: starts with no console window and detached, comes back at sign-in through a hidden launcher,
stops cleanly (a request, not TerminateProcess), and the Settings view has no systemd/linger text.

Pretend-Windows tests run on every OS; the `windows_only` ones run for real on the Windows runner.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mdpipeline import autostart, background, scheduler

CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW = 0x200, 0x08000000


@pytest.fixture
def started(monkeypatch):
    monkeypatch.delenv("MDPIPELINE_NO_BACKGROUND_START", raising=False)
    calls = []
    monkeypatch.setattr(autostart.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    return calls


# --- AC1: started with no console window, detached -----------------------------------------------------------------
def test_on_windows_start_now_has_no_console_window_and_its_own_process_group(started, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert autostart.start_now(python="C:\\Py\\python.exe", how="app_start") is True
    cmd, kw = started[0]
    assert cmd[:4] == ["C:\\Py\\python.exe", "-m", "mdpipeline", "background"] and "--started-by" in cmd
    assert kw["creationflags"] & CREATE_NO_WINDOW and kw["creationflags"] & CREATE_NEW_PROCESS_GROUP
    assert "start_new_session" not in kw            # POSIX only


def test_off_windows_start_now_still_uses_a_new_session(started, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    autostart.start_now(python="/p")
    assert started[0][1].get("start_new_session") is True and "creationflags" not in started[0][1]


# --- AC2: back at sign-in, hidden -----------------------------------------------------------------------------------
def test_the_windows_login_item_is_a_hidden_launcher_not_a_console_script(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    path = autostart.enable(home=tmp_path, platform="win32", python="C:\\Program Files\\Py\\python.exe")
    assert path.suffix == ".vbs" and path.parent == tmp_path / "AppData/Microsoft/Windows/Start Menu/Programs/Startup"
    text = path.read_text(encoding="utf-8")
    assert 'WScript.Shell' in text and ', 0, False' in text                         # window style 0 = hidden; do not wait
    assert '""C:\\Program Files\\Py\\python.exe"" -m mdpipeline background --started-by login_item' in text   # AC4: how=login_item


def test_the_launcher_line_is_a_single_well_formed_vbscript_statement(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    text = autostart.enable(home=tmp_path, platform="win32", python="C:\\Users\\o'neil & co\\python.exe").read_text(encoding="utf-8")
    assert text.count("\n") == 1 and text.count('"') % 2 == 0 and "o'neil & co" in text


# --- AC3: a clean stop ---------------------------------------------------------------------------------------------
def test_on_windows_stop_asks_through_a_request_file_and_never_kills(app_data, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    killed = []
    monkeypatch.setattr(os, "kill", lambda *a: killed.append(a))
    monkeypatch.setattr(background, "is_running", lambda: True)
    background._lock_path().write_text("424242", encoding="utf-8")
    assert background.stop() is True
    assert killed == [] and background._stop_path().exists()


def test_the_loop_ends_between_ticks_when_asked_and_clears_the_request(app_data):
    ticks = []

    def sleep(_):
        background._stop_path().write_text("1", encoding="utf-8")                   # the request arrives while it sleeps

    orig = scheduler.tick_all
    scheduler.tick_all = lambda **kw: ticks.append(1) or []
    try:
        assert background.run(max_ticks=None, sleep=sleep, interval=1) is True
    finally:
        scheduler.tick_all = orig
    assert ticks == [1] and not background._stop_path().exists() and not background.is_running()


def test_a_stale_request_from_an_earlier_stop_does_not_end_the_next_process_at_once(app_data):
    background._stop_path().parent.mkdir(parents=True, exist_ok=True)
    background._stop_path().write_text("1", encoding="utf-8")
    ticks = []
    orig = scheduler.tick_all
    scheduler.tick_all = lambda **kw: ticks.append(1) or []
    try:
        background.run(max_ticks=2, sleep=lambda s: None, interval=1)
    finally:
        scheduler.tick_all = orig
    assert len(ticks) == 2


def test_a_stop_request_during_a_tick_lets_the_current_brain_finish_and_skips_the_rest(app_data, monkeypatch):
    done = []
    monkeypatch.setattr(scheduler, "background_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "serve_brains", lambda now: ["/a", "/b", "/c"])

    def tick(brain, now=None):
        done.append(str(brain.root))
        if len(done) == 1:
            background._stop_path().write_text("1", encoding="utf-8")
        return []

    monkeypatch.setattr(scheduler, "tick", tick)
    monkeypatch.setattr(scheduler, "Brain", lambda p: type("B", (), {"root": p, "config_path": Path(__file__)})())
    scheduler.tick_all(now=1.0, should_stop=background.stop_requested)
    assert [Path(d).as_posix() for d in done] == ["/a"]


# --- AC5: no systemd / linger text on Windows ---------------------------------------------------------------------
def test_on_windows_settings_has_no_linger_or_systemd_text(app_data, monkeypatch):
    from mdpipeline import uiapi

    monkeypatch.setattr(sys, "platform", "win32")
    view = uiapi.background_view()
    assert not view.get("linger_hint") and not view.get("linger_notice") and "linger_text" not in view
    assert autostart.linger_state() == "unknown" and autostart.desktop_state() == "unknown"
    assert autostart.enable_user_service() is False


# --- the real thing, on the Windows runner only -------------------------------------------------------------------
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="runs for real only on Windows")


@windows_only
def test_windows_a_started_background_process_survives_and_stops_cleanly(app_data, monkeypatch):
    monkeypatch.delenv("MDPIPELINE_NO_BACKGROUND_START", raising=False)
    assert autostart.start_now(how="manual") is True
    assert background.wait_until_running(15)
    assert background.stop() is True
    deadline = time.time() + 40
    while background.is_running() and time.time() < deadline:
        time.sleep(0.5)
    assert not background.is_running()


@windows_only
def test_windows_the_hidden_launcher_starts_the_process(app_data, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    path = autostart.enable(python=sys.executable)
    subprocess.run(["wscript.exe", "//B", "//Nologo", str(path)], timeout=30, check=True)
    assert background.wait_until_running(20)
    background.stop()
    deadline = time.time() + 40
    while background.is_running() and time.time() < deadline:
        time.sleep(0.5)
    assert not background.is_running()
