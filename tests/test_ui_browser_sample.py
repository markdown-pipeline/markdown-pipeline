"""MD-124 in a real browser: Start -> Try with sample data -> Review -> Ready (agent, own source, remove), and the Home row."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, notices  # noqa: E402
from mdpipeline.config import load_config  # noqa: E402
from mdpipeline.usage import UsageStore  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_ui_browser_usage_events import queued, wait_for  # noqa: E402
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


def _try_sample(page, ui, tmp_path):
    open_app(page, ui)
    page.get_by_role("button", name="Try with sample data").click()
    expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()


def test_start_offers_the_sample_with_no_sign_in_and_no_network(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        row = page.get_by_role("button", name="Try with sample data")
        expect(row).to_contain_text("No sign-in, no network")
        shot(page, "sample-start")


def test_sample_goes_from_start_to_files_in_two_clicks_and_says_sample_everywhere(page, brain, tmp_path):
    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        _try_sample(page, ui, tmp_path)
        expect(page.locator("[aria-current=step]")).to_have_text("Review")
        expect(page.locator("#fr-review")).to_contain_text("Sample files will be written")
        expect(page.locator("#fr-review")).to_contain_text("No sign-in")
        expect(page.locator("#fr-review")).not_to_contain_text("Advanced")
        shot(page, "sample-review")
        page.get_by_role("button", name="Write 18 files").click()
        expect(page.locator("#fr-ready")).to_contain_text("Sample files in")
        expect(page.locator("#fr-ready")).to_contain_text("made-up data")
        expect(page.locator("#fr-ready")).not_to_contain_text("Stays current")
        expect(page.get_by_role("button", name="Connect my own source")).to_be_visible()
        expect(page.get_by_role("button", name="Remove the sample")).to_be_visible()
        shot(page, "sample-ready")
        wait_for("try_started")
        done = wait_for("try_completed")
        assert done[0]["properties"]["duration_s"] < 30
        assert load_config(brain).get("sample").schedule == "off"


def test_home_labels_the_sample_row_and_removes_it_in_one_click(page, brain, tmp_path):
    with running_ui(brain) as ui:
        _try_sample(page, ui, tmp_path)
        page.get_by_role("button", name="Write 18 files").click()
        expect(page.locator("#fr-ready")).to_contain_text("Sample files in")
        page.get_by_role("button", name="Go to Home").click()
        row = page.locator("li.prow")
        expect(row).to_contain_text("Sample")
        expect(row.locator(".sample-tag")).to_have_text("Sample")
        shot(page, "sample-home")
        row.get_by_role("button", name="Show a later sync").click()
        expect(page.locator("#toast")).to_contain_text("1 updated")
        expect(page.locator("#toast")).to_contain_text("1 deleted")
        row.get_by_role("button", name="Remove the sample").click()
        expect(page.get_by_role("heading", name="Connect your first source")).to_be_visible()
        assert load_config(brain).pipelines == []


def test_after_the_sample_connect_my_own_source_opens_start_without_the_sample_row(page, brain, tmp_path):
    with running_ui(brain) as ui:
        _try_sample(page, ui, tmp_path)
        page.get_by_role("button", name="Write 18 files").click()
        expect(page.locator("#fr-ready")).to_contain_text("Sample files in")
        page.get_by_role("button", name="Connect my own source").click()
        expect(page.get_by_role("heading", name="Connect your first source")).to_be_visible()
        expect(page.get_by_role("button", name="Jira Cloud")).to_be_visible()
        expect(page.get_by_role("button", name="Try with sample data")).to_have_count(0)
