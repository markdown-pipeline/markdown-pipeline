"""MD-121 AC2/AC7: the login item that starts the background process, and its opt-out (nothing outside the user's own folders)."""
import plistlib

import pytest

from mdpipeline import autostart


def test_linux_login_item_is_an_xdg_autostart_entry_running_the_background_command(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = autostart.enable(home=tmp_path, platform="linux", python="/usr/bin/python3")
    assert path == tmp_path / ".config/autostart/mdpipeline-background.desktop"
    text = path.read_text(encoding="utf-8")
    assert "Exec=/usr/bin/python3 -m mdpipeline background" in text and "Type=Application" in text


def test_linux_login_item_honours_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert autostart.enable(home=tmp_path, platform="linux", python="/p").parent == tmp_path / "cfg/autostart"


def test_macos_login_item_is_a_launch_agent_that_runs_at_load(tmp_path):
    path = autostart.enable(home=tmp_path, platform="darwin", python="/usr/bin/python3")
    assert path == tmp_path / "Library/LaunchAgents/com.markdownpipeline.background.plist"
    data = plistlib.loads(path.read_bytes())
    assert data["ProgramArguments"] == ["/usr/bin/python3", "-m", "mdpipeline", "background", "--started-by", "login_item"] and data["RunAtLoad"] is True


def test_windows_login_item_is_a_startup_folder_script(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    path = autostart.enable(home=tmp_path, platform="win32", python="C:\\Python\\pythonw.exe")
    assert path.parent == tmp_path / "AppData/Microsoft/Windows/Start Menu/Programs/Startup"
    assert "-m mdpipeline background" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_enable_is_idempotent_and_disable_removes_the_item(tmp_path, monkeypatch, platform):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    kw = dict(home=tmp_path, platform=platform)
    assert autostart.is_enabled(**kw) is False
    autostart.enable(**kw, python="/p")
    autostart.enable(**kw, python="/p")
    assert autostart.is_enabled(**kw) is True
    assert autostart.disable(**kw) is True
    assert autostart.is_enabled(**kw) is False
    assert autostart.disable(**kw) is False        # nothing left to remove


def test_an_unknown_platform_is_refused_not_guessed(tmp_path):
    with pytest.raises(autostart.AutostartError):
        autostart.enable(home=tmp_path, platform="plan9", python="/p")


def test_start_now_starts_a_detached_background_process(monkeypatch):
    monkeypatch.delenv("MDPIPELINE_NO_BACKGROUND_START")
    started = []
    monkeypatch.setattr(autostart.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    assert autostart.start_now(python="/p") is True
    cmd, kw = started[0]
    assert cmd == ["/p", "-m", "mdpipeline", "background", "--started-by", "app_start"] and kw.items() >= autostart.detached_kwargs().items()   # a new session on POSIX, no console window on Windows


def test_start_now_does_nothing_when_the_kill_switch_is_set(monkeypatch):
    started = []
    monkeypatch.setattr(autostart.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert autostart.start_now() is False and started == []
