"""MD-217: the app page keeps working when one of its scripts does not load (a busy machine, a local server that did not answer in time).
`brBrowseButton is not defined` was seen once under load: browse.js is the last of six scripts. A missing Browse script only removes the Browse buttons;
a missing core script shows a calm "Reload the page" line instead of a broken screen."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from test_ui_browser_firstrun import jira, quiet  # noqa: E402,F401
from test_ui_browser_firstrun_vault import fill_jira, no_keychain, to_review  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

RELOAD_LINE = "Part of this page didn't load. Reload the page to fix it."


def test_without_browse_js_first_run_still_works_and_there_are_no_browse_buttons(page, brain, jira, no_keychain, tmp_path):
    page.route("**/browse.js", lambda route: route.abort())
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        to_review(page, tmp_path)                                                  # through the folder step (typed path) to Review
        expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()
        assert page.locator("button[data-browse]").count() == 0, page.get_by_role("button", name="Browse").first.inner_text()          # the Browse… buttons
    assert page.errors == []


@pytest.mark.parametrize("script", ["firstrun.js", "agents.js", "remove.js", "feedback.js"])
def test_without_a_core_script_the_page_says_to_reload_instead_of_breaking(page, brain, script):
    page.route(f"**/{script}", lambda route: route.abort())
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#main")).to_contain_text(RELOAD_LINE)
        expect(page.get_by_role("button", name="Reload the page")).to_have_count(1)
    assert page.errors == []


def test_with_every_script_loaded_there_is_no_reload_line(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        assert RELOAD_LINE not in page.locator("#main").inner_text()


# --- DS-P-1106-4: the missing script is counted (error_shown: ui_area page, error_class script_missing, script), once per page load ---------------------
def reported():
    import json
    from mdpipeline import usage
    if not (usage.data_dir() / "usage.json").exists():
        return []
    from test_background_headless import own, queued
    return [own(e) for e in queued("error_shown")]


@pytest.fixture
def sharing(app_data):
    from mdpipeline.usage import UsageStore
    UsageStore().set_consent("share")


@pytest.mark.parametrize("script, name", [("firstrun.js", "firstrun"), ("agents.js", "other"), ("remove.js", "other"), ("feedback.js", "other")])
def test_a_missing_core_script_is_counted_once_with_its_fixed_name(page, brain, sharing, script, name):
    page.route(f"**/{script}", lambda route: route.abort())
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#script-missing")).to_be_visible()
        page.wait_for_timeout(600)
    assert reported() == [{"ui_area": "page", "error_class": "script_missing", "script": name}]


def test_a_missing_browse_script_is_counted_too_without_a_notice(page, brain, sharing):
    page.route("**/browse.js", lambda route: route.abort())
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.wait_for_timeout(600)
        assert page.locator("#script-missing").count() == 0
    assert reported() == [{"ui_area": "page", "error_class": "script_missing", "script": "browse"}]


def test_nothing_is_counted_when_every_script_loaded(page, brain, sharing):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.wait_for_timeout(600)
    assert reported() == []


def test_nothing_is_counted_without_consent(page, brain, app_data):
    page.route("**/firstrun.js", lambda route: route.abort())
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.wait_for_timeout(600)
    assert reported() == []
