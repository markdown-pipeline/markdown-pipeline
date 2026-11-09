"""MD-181 AC5, marketing's MM-135 words for Windows (MD-181 comment 10799): "sign in to Windows" instead of "log in (to your desktop)".

Windows only: Linux and macOS keep their text word for word (tests/test_copy_md164.py, test_copy_md167.py pin those)."""
import re
from pathlib import Path

import pytest

from mdpipeline import cli, install_info, scheduler, uiapi

STATIC = Path(__file__).parent.parent / "src" / "mdpipeline" / "ui_static"
FIRSTRUN_JS = (STATIC / "firstrun.js").read_text()
APP_JS = (STATIC / "app.js").read_text()


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")


def test_announcement_on_windows_says_sign_in_to_windows(windows):
    text = scheduler.announcement()
    assert text == ("Your files now stay current by themselves: a small background process syncs every hour. It keeps running when this "
                    "window is closed and starts when you sign in to Windows. You can change how often or turn it off any time.")


def test_announcement_elsewhere_is_unchanged(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert scheduler.announcement() == scheduler.ANNOUNCEMENT and "starts at login" in scheduler.ANNOUNCEMENT


def test_background_on_line_on_windows(windows):
    assert cli.background_on_text() == "Background sync is on: it keeps running when this window is closed and starts again when you sign in to Windows."


def test_background_off_line_on_windows(windows):
    assert cli.background_off_text() == "Background sync is off and it no longer starts when you sign in to Windows."


def test_schedule_help_on_windows(windows):
    assert cli.schedule_description().endswith("starts again when you sign in to Windows.")


def test_cli_lines_elsewhere_are_unchanged(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert cli.background_on_text().endswith("starts again when you log in.")
    assert cli.background_off_text() == "Background sync is off and its login item is removed."
    assert cli.schedule_description().endswith("starts again when you log in.")


def test_the_ui_state_tells_the_page_which_os_it_is_on(brain, monkeypatch):
    monkeypatch.setattr(install_info, "os_name", lambda *a, **k: "windows")
    assert uiapi.state_view(brain)["os"] == "windows"


def test_first_run_swaps_the_wording_on_windows_and_keeps_the_old_literals():
    flat = re.sub(r"\s+", " ", FIRSTRUN_JS)
    assert flat.count("starts when you log in to your desktop. You can turn it off any time.") == 2          # unchanged elsewhere
    assert 'S.state.os === "windows" ? thenText.replace("log in to your desktop", "sign in to Windows")' in flat


def test_settings_source_has_both_wordings():
    assert "(starts when you sign in to Windows)" in APP_JS and "(starts when you log in to your desktop)" in APP_JS
