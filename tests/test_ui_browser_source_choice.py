"""MD-99 in a real browser: Add source -> Folder / Jira, no pre-filled row, any row removable, Jira as the only source."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import jira_api  # noqa: E402
from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_jira_sync import EMAIL, SITE, TOKEN  # noqa: E402
from test_ui_browser import add_source, browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def fill_jira(page):
    for key, value in dict(site=SITE, jql="project = MD", label="md-jira").items():
        page.locator(f'[data-bind="jira-{key}"]').fill(value)
    page.locator('[data-bind="jira-email"]').fill(EMAIL)
    page.locator('[data-bind="jira-token"]').fill(TOKEN)


def test_a_new_pipeline_starts_with_no_source_row_and_save_is_disabled_with_a_plain_message(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        expect(page.locator(".srcrow")).to_have_count(0)
        expect(page.locator("#m-sources")).to_contain_text("Start by adding a source")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Save & sync")).to_be_disabled()


def test_add_source_offers_folder_and_jira(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        page.get_by_text("+ Add source").click()
        group = page.get_by_role("group", name="Kind of source")
        expect(group.get_by_role("button")).to_have_text(["Folder", "Jira"])
        group.get_by_role("button", name="Jira").click()
        expect(page.locator(".jirarow")).to_have_count(1)
        expect(page.locator("#m-sources")).not_to_contain_text("Start by adding a source")


def test_jira_only_name_is_suggested_from_the_label_and_the_pipeline_syncs(page, brain, fake):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page, "Jira")
        fill_jira(page)
        expect(page.locator("#f-name")).to_have_value("md-jira")
        expect(page.locator("#pv")).to_contain_text("create")
        page.get_by_role("button", name="Save & sync").click()
        expect(page.locator("#toast")).to_contain_text("synced")
        expect(page.locator("li.prow", has_text="md-jira")).to_contain_text("Jira md-jira")
    assert [s.type for s in load_config(brain).get("md-jira").sources] == ["jira"]


def test_the_only_source_can_be_removed_and_a_jira_source_replaces_it_keeping_the_folders_files(page, brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(6)})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow", has_text="p").get_by_role("button", name="Edit").click()
        remove = page.get_by_role("button", name="Remove folder 1")
        expect(remove).to_be_enabled()
        remove.click()
        expect(page.locator("#m-sources")).to_contain_text("Start by adding a source")
        expect(page.locator("#m-removed")).to_contain_text("are kept in your output folder")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        add_source(page, "Jira")
        fill_jira(page)
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("p saved")
    assert [s.type for s in load_config(brain).get("p").sources] == ["jira"]
    assert len(list((brain.root / "raw/p/docs").glob("*.md"))) == 6


def test_needs_you_for_a_failing_jira_only_pipeline_says_nothing_was_deleted_and_has_no_folder_text(page, brain, fake, add_pipeline):
    from test_jira_sync import jira_source

    add_pipeline(Pipeline("p", [jira_source(label="md-jira")], name_policy="date-name", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    fake.fail("GET", "/search/jql", status=503, times=9)
    sync_pipeline(brain, "p", trigger="ui")
    with running_ui(brain) as ui:
        open_app(page, ui, "activity")
        card = page.locator("article.ecard", has_text="md-jira")
        expect(card).to_contain_text("Nothing it owns was deleted")
        expect(card).not_to_contain_text("These files were skipped")
        expect(card).not_to_contain_text("folder")
