"""MD-212: the host's systemd and desktop never decide a test. The CI runner has neither (autostart.cannot_start_at_login() would be True there), a
developer machine has both. Here the CI conditions are simulated, WITHOUT the real_login_start marker, and the answer must still be False."""
import sys

from mdpipeline import autostart
from test_background_headless import FakeSystemd
from uihelper import running_ui


def test_a_no_systemd_no_desktop_linux_host_does_not_change_an_ordinary_test(brain, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(available=False))
    assert autostart.cannot_start_at_login() is False
    with running_ui(brain) as ui:
        bg = ui.get("/api/state")[1]["background"]
    assert bg["no_login_start"] is False and "no_login_start_text" not in bg
