"""MD-212 (AC3, AC4, AC6): on Linux with no systemd AND no confirmed desktop session, nothing can start Markdown Pipeline at login. `mdpipeline background`,
`schedule` and Settings say so, with the command to start it and the way to add it to the person's own startup. A desktop (XFCE) shows nothing new."""
import sys

import pytest

from mdpipeline import autostart, cli, uiapi
from mdpipeline.usage import UsageStore

from test_background_headless import FakeSystemd
from test_md212_not_running import set_state
from uihelper import running_ui

pytestmark = pytest.mark.real_login_start          # these tests are about the real check, with the host faked by the fixture below

TEXT = ("Markdown Pipeline can't start itself when you log in on this system, so your files won't update after a restart until you start it. "
        "To start it now: mdpipeline background. To start it at every login, add that command to your own startup or service manager.")


@pytest.fixture
def runit_box(monkeypatch, app_data):
    """Linux, no systemd (the calls get no answer), no display."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(available=False))
    set_state()


def test_no_systemd_and_no_desktop_session_cannot_start_at_login(runit_box):
    assert autostart.cannot_start_at_login() is True


def test_a_desktop_session_shows_nothing_new(runit_box, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")                    # the owner's XFCE: XDG autostart works
    assert autostart.cannot_start_at_login() is False


def test_systemd_shows_nothing_new(runit_box, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="yes"))
    assert autostart.cannot_start_at_login() is False


def test_a_logind_graphical_session_shows_nothing_new(runit_box, monkeypatch):
    monkeypatch.setattr(autostart, "desktop_state", lambda: "yes")
    assert autostart.cannot_start_at_login() is False


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_windows_and_macos_are_unchanged(runit_box, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    assert autostart.cannot_start_at_login() is False


def test_the_text_is_marketings_draft_and_has_no_jargon():
    assert autostart.NO_LOGIN_START_LINE == TEXT and "XDG" not in TEXT and "systemd" not in TEXT.split("service manager")[0]


def test_schedule_show_and_background_on_print_it(runit_box, brain, capsys, monkeypatch):
    monkeypatch.setattr(autostart, "start_now", lambda **kw: False)
    cli.main(["--brain", str(brain.root), "schedule"])
    cli.main(["--brain", str(brain.root), "schedule", "background", "on"])
    assert capsys.readouterr().out.count(TEXT.replace("`", "")) == 2


def test_a_manual_mdpipeline_background_prints_it_before_it_runs(runit_box, brain, capsys, monkeypatch):
    from mdpipeline import background
    monkeypatch.setattr(background, "run", lambda **kw: True)
    cli.main(["--brain", str(brain.root), "background"])
    assert TEXT in capsys.readouterr().out


def test_the_login_item_starting_it_prints_nothing(runit_box, brain, capsys, monkeypatch):
    from mdpipeline import background
    monkeypatch.setattr(background, "run", lambda **kw: True)
    cli.main(["--brain", str(brain.root), "background", "--started-by", "login_item"])
    assert TEXT not in capsys.readouterr().out


def test_a_desktop_prints_nothing_in_the_cli(runit_box, brain, capsys, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    cli.main(["--brain", str(brain.root), "schedule"])
    assert "can't start itself when you log in" not in capsys.readouterr().out


def test_settings_state_carries_the_text_only_when_it_applies(runit_box, brain, monkeypatch):
    with running_ui(brain) as ui:
        bg = ui.get("/api/state")[1]["background"]
        assert bg["no_login_start"] is True and bg["no_login_start_text"] == TEXT
        monkeypatch.setenv("DISPLAY", ":0")
        bg = ui.get("/api/state")[1]["background"]
    assert bg["no_login_start"] is False and "no_login_start_text" not in bg


def test_a_faked_windows_is_windows_here_too(runit_box, monkeypatch):
    """Settings fakes Windows through install_info.os_name (browser tests); a no-desktop Linux host must not show the Linux line then."""
    from mdpipeline import install_info
    monkeypatch.setattr(install_info, "os_name", lambda *a, **k: "windows")
    assert autostart.cannot_start_at_login() is False

