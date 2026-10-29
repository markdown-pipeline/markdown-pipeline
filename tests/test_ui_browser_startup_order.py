"""Found by CI (2026-10-29, v0.1.12 publish run): app.js started loading data at the end of its own file, but the other scripts (firstrun.js, ...) load after it.
If the server answered before firstrun.js had run, the page called `enterFirstRun` too early: "enterFirstRun is not defined", and a blank first screen.
The app must start only when every script has been loaded."""
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_ui_browser import browser, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


def test_a_slow_second_script_does_not_break_the_first_screen(page, brain):
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def slow(route):
        time.sleep(1.5)                      # the server has long answered /api/state when this script arrives
        route.continue_()

    page.route("**/firstrun.js", slow)
    with running_ui(brain) as ui:
        page.goto(ui.base)
        expect(page.locator("#main")).not_to_be_empty(timeout=20000)
        expect(page.locator("h1").first).to_be_visible()
    assert errors == [], errors
