"""MD-181 AC5 in a real browser: Settings says "sign in to Windows" on Windows and keeps the desktop wording elsewhere."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import install_info  # noqa: E402
from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


def open_settings(page):
    page.get_by_role("link", name="Settings").or_(page.get_by_role("button", name="Settings")).first.click()
    expect(page.locator("#bg-settings")).to_be_visible()


def test_settings_on_windows(page, brain, monkeypatch):
    monkeypatch.setattr(install_info, "os_name", lambda *a, **k: "windows")
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-settings")).to_contain_text("Keep syncing when this window is closed (starts when you sign in to Windows).")
        assert "log in" not in page.locator("#bg-settings").inner_text()


def test_settings_elsewhere_keeps_the_desktop_wording(page, brain, monkeypatch):
    monkeypatch.setattr(install_info, "os_name", lambda *a, **k: "linux")
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-settings")).to_contain_text("(starts when you log in to your desktop)")
