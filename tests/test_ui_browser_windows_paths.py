"""MD-182 trace (found by the MD-180 follow-up): a Windows folder path typed in the editor gives a sensible label and pipeline name.

The editor derived both from the text after the last "/". A Windows path uses "\\", so the whole path became the label, the server
refused it (bad-label), and the preview said "No source folder can be read". These tests type Windows-style paths, which need not
exist, so they run on every OS.
"""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_ui_browser import add_source, browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.mark.parametrize("typed", ["C:\\Users\\me\\Team Docs", "C:\\Users\\me\\Team Docs\\", "C:\\Users\\me/Team Docs", "/home/me/Team Docs/"])
def test_the_label_is_the_last_folder_name_for_any_kind_of_path(page, brain, typed):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.locator("#f-src0").fill(typed)
        expect(page.locator("#l-src0")).to_have_value("Team Docs")


def test_the_pipeline_name_comes_from_the_last_folder_of_a_windows_path(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.locator("#f-src0").fill("C:\\Users\\me\\team-docs")
        expect(page.locator("#f-name")).to_have_value("team-docs")


def test_two_windows_folders_name_the_pipeline_after_their_shared_parent(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.locator("#f-src0").fill("C:\\work\\project\\raw")
        add_source(page)
        page.locator("#f-src1").fill("C:\\work\\project\\wiki")
        expect(page.locator("#f-name")).to_have_value("project")
