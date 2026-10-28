"""MD-166 in a real browser: on a server (no desktop session) with linger off, the line is where the person is: the first-run Ready step and
Home (hideable), and Settings keeps it. With linger on, or on a desktop, nothing extra appears."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, notices  # noqa: E402
from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_background_headless import FakeSystemd, turn_on  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_ui_browser_firstrun import notes, quiet  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def server(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="no"))
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def one(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "acme", trigger="cli")
    turn_on()
    return brain


def test_home_shows_the_line_with_both_commands_and_hide_removes_it_for_good(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        line = page.locator("#bg-linger-home")
        expect(line).to_contain_text("stops when you log out")
        expect(line).to_contain_text("loginctl enable-linger $USER")
        expect(line).to_contain_text("sudo loginctl enable-linger $USER")
        shot(page, "linger-home")
        line.get_by_role("button", name="Hide").click()
        expect(page.locator("#bg-linger-home")).to_have_count(0)
        page.reload()
        expect(page.locator("main")).to_contain_text("Background sync is on")
        expect(page.locator("#bg-linger-home")).to_have_count(0)


def test_settings_keeps_the_line_after_it_is_hidden_on_home(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        page.locator("#bg-linger-home").get_by_role("button", name="Hide").click()
        page.get_by_role("button", name="Settings").click()
        expect(page.locator("#bg-linger")).to_contain_text("loginctl enable-linger $USER")


def test_a_desktop_session_gets_no_line_on_home(page, one, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    with running_ui(one) as ui:
        open_app(page, ui)
        expect(page.locator("#bg-linger-home")).to_have_count(0)


def test_linger_on_gets_no_line_on_home(page, one, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="yes"))
    with running_ui(one) as ui:
        open_app(page, ui)
        expect(page.locator("#bg-linger-home")).to_have_count(0)


def test_the_first_run_ready_step_shows_the_line(page, brain, notes, tmp_path):
    from test_ui_browser_firstrun_flow import folder_to_target

    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        page.get_by_role("textbox", name="Output folder").fill(str(dest))
        page.get_by_role("button", name="Use this folder").click()
        page.get_by_role("button", name=f"Write 7 files to {dest}").click()
        expect(page.locator("#fr-ready")).to_contain_text("7 files in")
        expect(page.locator("#fr-linger")).to_contain_text("loginctl enable-linger $USER")
        shot(page, "linger-ready")
