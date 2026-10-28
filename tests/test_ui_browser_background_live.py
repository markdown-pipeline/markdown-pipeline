"""MD-162 in a real browser: Home updates by itself. After "Start it" the "not running" line is replaced by the next sync time (no reload);
a failed start says so with one next step; the button cannot be pressed twice; notices and the background starting/stopping show within seconds."""
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from bg_helper import FakeBackgroundProcess  # noqa: E402
from mdpipeline import autostart, notices  # noqa: E402
from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def one(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "acme", trigger="cli")
    return brain


@pytest.fixture
def proc():
    p = FakeBackgroundProcess()
    yield p
    p.stop()


def mark_page(page):
    """A value that a reload would erase: proves nothing reloaded."""
    page.evaluate("window.__no_reload = 42")


def never_reloaded(page):
    return page.evaluate("window.__no_reload") == 42


def test_after_start_it_the_not_running_line_gives_way_to_the_next_sync_time_with_no_reload(page, one, proc, monkeypatch):
    monkeypatch.setattr(autostart, "start_now", lambda python=None: proc.start(start_after=1.0) or True)     # the process takes a moment to come up
    with running_ui(one) as ui:
        open_app(page, ui)
        mark_page(page)
        expect(page.locator("#bg-not-running")).to_be_visible()
        page.get_by_role("button", name="Start it").click()
        expect(page.locator("#bg-not-running")).to_have_count(0, timeout=8000)
        expect(page.locator("#bg-next")).to_contain_text(re.compile(r"Next sync (at \d{1,2}:\d{2}|in a moment)"))
        assert never_reloaded(page)
        shot(page, "home-background-started")


def test_while_starting_the_button_shows_progress_and_cannot_be_pressed_twice(page, one, proc, monkeypatch):
    starts = []
    monkeypatch.setattr(autostart, "start_now", lambda python=None: starts.append(1) or proc.start(start_after=2.0) or True)
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Start it").click()
        starting = page.locator("#bg-not-running").get_by_role("button")
        expect(starting).to_be_disabled()
        expect(page.locator("#bg-not-running")).to_contain_text("Starting")
        page.wait_for_timeout(300)
        starting.click(force=True, no_wait_after=True)                  # a second press on a disabled button does nothing
        expect(page.locator("#bg-next")).to_be_visible(timeout=8000)
    assert len(starts) == 1


def test_a_start_that_never_comes_up_says_so_with_one_next_step_and_lets_you_try_again(page, one, monkeypatch):
    monkeypatch.setattr(autostart, "start_now", lambda python=None: False)
    page.add_init_script("window.__bgStartWaitMs = 1500")
    with running_ui(one) as ui:
        open_app(page, ui)
        mark_page(page)
        page.get_by_role("button", name="Start it").click()
        failed = page.locator("#bg-not-running")
        expect(failed).to_contain_text("did not start", timeout=8000)
        expect(failed).to_contain_text("mdpipeline background")                       # the one next step
        expect(failed.get_by_role("button", name="Try again")).to_be_enabled()
        assert never_reloaded(page)


def test_a_needs_you_notice_appears_on_an_open_home_page_without_a_reload(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        mark_page(page)
        expect(page.locator("#needs-you")).to_have_count(0)
        for _ in range(2):
            ActivityLog(one).append({"type": "run", "pipeline": "acme", "trigger": "schedule", "result": "error", "counts": {}, "conflicts": [],
                                     "errors": [{"source": "a", "error": "boom", "category": "network"}]})
        notices.check(one)
        expect(page.locator('[data-notice="sync_failed"]')).to_be_visible(timeout=10000)
        assert never_reloaded(page)


def test_a_notice_that_clears_disappears_without_a_reload(page, one):
    from mdpipeline.schedulestore import SchedulerStore

    for _ in range(2):
        ActivityLog(one).append({"type": "run", "pipeline": "acme", "trigger": "schedule", "result": "error", "counts": {}, "conflicts": [],
                                 "errors": [{"source": "a", "error": "boom", "category": "network"}]})
    notices.check(one)
    with running_ui(one) as ui:
        open_app(page, ui)
        mark_page(page)
        expect(page.locator('[data-notice="sync_failed"]')).to_be_visible()
        with SchedulerStore().update() as data:
            data["runtime"][str(one.root)]["notices"] = {}
        expect(page.locator('[data-notice="sync_failed"]')).to_have_count(0, timeout=10000)
        assert never_reloaded(page)


def test_a_background_process_that_starts_or_stops_elsewhere_shows_without_a_reload(page, one, proc):
    with running_ui(one) as ui:
        open_app(page, ui)
        mark_page(page)
        expect(page.locator("#bg-not-running")).to_be_visible()
        proc.start()                                                          # e.g. `mdpipeline background` in a terminal
        expect(page.locator("#bg-not-running")).to_have_count(0, timeout=10000)
        expect(page.locator("#bg-next")).to_be_visible()
        proc.stop()
        expect(page.locator("#bg-not-running")).to_be_visible(timeout=10000)
        assert never_reloaded(page)
