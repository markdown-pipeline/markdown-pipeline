"""MD-218 in a real browser: inside the restart window Settings says the restart is under way; after it, the one next step (MD-204) shows as before."""
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

import mdpipeline  # noqa: E402
from mdpipeline import background  # noqa: E402
from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from test_ui_browser_background_outdated import open_settings  # noqa: E402
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def old_process(monkeypatch):
    monkeypatch.setattr(mdpipeline, "__version__", "0.1.18")
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.17")


def test_settings_says_restarting_inside_the_window_and_does_not_ask(page, brain, old_process):
    background.mark_restart_requested(time.time())
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-restarting")).to_have_text("On, and restarting on version 0.1.18. A sync in progress finishes first.")
        expect(page.locator("#bg-outdated")).to_have_count(0)


def test_settings_asks_for_the_restart_after_the_window(page, brain, old_process):
    background.mark_restart_requested(time.time() - background.RESTART_PENDING_SECONDS - 5)
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#bg-outdated")).to_contain_text("To restart it, open a terminal and run: mdpipeline schedule background restart")
        expect(page.locator("#bg-restarting")).to_have_count(0)
