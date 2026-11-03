"""MD-204 AC3 in a real browser: when the background process runs an older version than the installed one, Settings says so with one next step."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

import mdpipeline  # noqa: E402
from mdpipeline import background  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


def open_settings(page):
    page.get_by_role("link", name="Settings").or_(page.get_by_role("button", name="Settings")).first.click()
    expect(page.locator("#bg-settings")).to_be_visible()


def test_settings_says_the_background_process_runs_an_older_version_and_gives_one_next_step(page, brain, monkeypatch):
    monkeypatch.setattr(mdpipeline, "__version__", "0.1.16")
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.15")
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-outdated")).to_have_text(
            "On, but still running the older version 0.1.15 (installed: 0.1.16). To restart it, open a terminal and run: mdpipeline schedule background restart")
        shot(page, "settings-background-older-version")


def test_settings_is_quiet_when_the_running_version_is_current(page, brain, monkeypatch):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: mdpipeline.__version__)
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-outdated")).to_have_count(0)
        expect(page.locator("#bg-settings")).to_contain_text("On, and running now.")
