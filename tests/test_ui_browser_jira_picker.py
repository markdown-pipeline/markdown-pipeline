"""MD-141 in a real browser: after the Jira token check the person ticks projects instead of typing a key or JQL."""
import json
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import autostart, jira_api, notices, usage  # noqa: E402
from mdpipeline.usage import UsageStore  # noqa: E402
from test_jira_sync import EMAIL, TOKEN  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def site():
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_project("ACME", "Acme Platform")
    f.add_project("OPS", "Operations")
    f.add_project("HR", "People")
    for n in range(6):
        f.add_issue("ACME", f"Platform issue {n}")
    for n in range(2):
        f.add_issue("OPS", f"Ops issue {n}")
    f.add_issue("HR", "Hiring plan")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


def to_scope(page, ui):
    open_app(page, ui)
    page.get_by_role("button", name="Jira Cloud").click()
    page.get_by_label("Jira site").fill("acme")
    page.get_by_label("Your Atlassian email").fill(EMAIL)
    page.get_by_label("API token").fill(TOKEN)
    page.get_by_role("button", name="Test and continue").click()
    expect(page.get_by_role("heading", name="Which Jira issues should become files?")).to_be_visible()


def to_review(page, ui):
    """Through Projects and Where files go with their defaults (MD-159), to Review."""
    to_scope(page, ui)
    page.get_by_role("button", name=re.compile("^Continue with")).click()
    page.get_by_role("button", name="Use this folder").click()
    expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()


def some_projects(page):
    page.get_by_role("radio", name="Only some projects").check()


def test_the_projects_step_lists_readable_projects_as_name_and_key_and_the_default_still_works_untouched(page, brain, site):
    with running_ui(brain) as ui:
        to_scope(page, ui)
        expect(page.locator("#fr-count")).to_contain_text("9 issues")                              # nothing ticked: the default, no extra click
        expect(page.get_by_role("button", name="Continue with 9 issues")).to_be_enabled()
        some_projects(page)
        expect(page.locator("#fr-projects")).to_contain_text("Acme Platform")
        expect(page.locator("#fr-projects")).to_contain_text("ACME")
        expect(page.locator("#fr-projects input[type=checkbox]")).to_have_count(3)
        expect(page.get_by_role("button", name=re.compile("^Continue"))).to_be_disabled()           # "Only some" with nothing ticked
        expect(page.locator("#fr-count")).to_contain_text("Tick at least one project, or choose All projects.")
        shot(page, "jira-picker-projects")


def test_ticking_two_projects_builds_the_query_shows_it_read_only_and_recounts(page, brain, site, tmp_path):
    with running_ui(brain) as ui:
        to_scope(page, ui)
        some_projects(page)
        page.get_by_label("Acme Platform (ACME)").check()
        page.get_by_label("Operations (OPS)").check()
        expect(page.locator("#fr-jql-built")).to_have_text("project in (ACME, OPS) AND updated >= -90d ORDER BY updated DESC")
        expect(page.locator("#fr-jql-built")).to_have_attribute("readonly", "")
        expect(page.locator("#fr-projects-line")).to_have_text("Every issue in ACME and OPS, newest change first.")
        expect(page.locator("#fr-count")).to_contain_text("8 issues")
        page.get_by_role("button", name="Continue with 8 issues").click()
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "o"))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#fr-summary [data-row=from]")).to_contain_text("2 projects (ACME, OPS) · updated in the last 90 days")
        page.get_by_role("button", name="Write 8 files").click()
        expect(page.locator("#fr-ready")).to_contain_text("8 files in")
    from mdpipeline.config import load_config
    assert load_config(brain).pipelines[0].sources[0].options["jql"] == "project in (ACME, OPS) AND updated >= -90d ORDER BY updated DESC"


def test_search_filters_by_name_or_key_and_select_all_ticks_the_visible_ones(page, brain, site):
    with running_ui(brain) as ui:
        to_scope(page, ui)
        some_projects(page)
        page.get_by_label("Search projects").fill("oper")
        expect(page.locator("#fr-projects input[type=checkbox]")).to_have_count(1)
        page.get_by_role("button", name="Select all").click()
        expect(page.locator("#fr-jql-built")).to_have_text("project in (OPS) AND updated >= -90d ORDER BY updated DESC")


def test_a_long_list_loads_more_on_request(page, brain):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for i in range(250):
        f.add_project(f"P{i:03d}", f"Project {i:03d}")
    f.add_issue("P000", "one")
    jira_api.set_transport(f, sleep=lambda s: None)
    try:
        with running_ui(brain) as ui:
            to_scope(page, ui)
            some_projects(page)
            expect(page.locator("#fr-projects input[type=checkbox]")).to_have_count(100)
            page.get_by_role("button", name="Show more projects").click()
            expect(page.locator("#fr-projects input[type=checkbox]")).to_have_count(200)
            page.get_by_role("button", name="Show more projects").click()
            expect(page.locator("#fr-projects input[type=checkbox]")).to_have_count(250)
            expect(page.get_by_role("button", name="Show more projects")).to_have_count(0)
    finally:
        jira_api.set_transport(None)


def test_own_jql_wins_when_edited_and_ticking_afterwards_asks_before_overwriting(page, brain, site):
    with running_ui(brain) as ui:
        to_scope(page, ui)
        some_projects(page)
        page.locator(".fr-advanced summary").click()
        page.get_by_label("Which issues (JQL)").fill("project = HR")
        expect(page.locator("#fr-count")).to_contain_text("1 issue")
        page.get_by_label("Acme Platform (ACME)").check()
        expect(page.locator("#fr-replace-jql")).to_contain_text("replace the query you wrote")
        page.get_by_role("button", name="Keep my query").click()
        expect(page.get_by_label("Which issues (JQL)")).to_have_value("project = HR")
        expect(page.get_by_label("Acme Platform (ACME)")).not_to_be_checked()
        page.get_by_label("Acme Platform (ACME)").check()
        page.get_by_role("button", name="Replace my query").click()
        expect(page.get_by_label("Which issues (JQL)")).to_have_value("project in (ACME) AND updated >= -90d ORDER BY updated DESC")
        expect(page.locator("#fr-count")).to_contain_text("6 issues")


def test_a_token_without_the_scope_is_a_calm_notice_with_the_fix_and_raw_jql_kept(page, brain, site):
    site.fail("GET", "/project/search", status=403, times=9)
    with running_ui(brain) as ui:
        to_scope(page, ui)
        box = page.locator("#fr-projects")
        expect(box).to_contain_text("read:jira-work")
        expect(box.get_by_role("link", name="Fix the token")).to_have_attribute("href", "/jira-token-help.md")
        expect(box.locator("[role=alert]")).to_have_count(0)                                           # calm: not a red error
        expect(page.locator("#fr-count")).to_contain_text("9 issues")
        page.locator(".fr-advanced summary").click()
        expect(page.get_by_label("Which issues (JQL)")).to_be_visible()


def test_the_pick_and_the_own_query_are_reported_as_a_method_and_a_bucket_only(page, brain, site):
    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        to_scope(page, ui)
        some_projects(page)
        page.get_by_label("Acme Platform (ACME)").check()
        expect(page.locator("#fr-jql-built")).to_be_visible()
        page.locator(".fr-advanced summary").click()
        page.get_by_label("Which issues (JQL)").fill("project = HR")
        expect(page.locator("#fr-count")).to_contain_text("1 issue")
    entries = [e for e in json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", []) if e["event"] == "jira_project_pick"]
    methods = [e["properties"]["method"] for e in entries]
    assert "picker" in methods and "advanced_jql" in methods and methods.count("advanced_jql") == 1
    text = json.dumps(entries)
    assert "ACME" not in text and "HR" not in text and "project" not in text.replace("jira_project_pick", "")


# --- the editor (AC1: first run and editor) ----------------------------------------------------------------------------
from test_ui_browser_jira import fill  # noqa: E402
from test_jira_sync import SITE  # noqa: E402
from test_ui_browser import add_source  # noqa: E402


def open_jira_source(page, ui, jql=""):
    open_app(page, ui)
    page.get_by_role("button", name="Advanced setup").first.click()
    add_source(page, "Jira")
    fill(page, site=SITE, jql=jql, email=EMAIL, token=TOKEN)
    page.locator(".jirarow").get_by_role("button", name="Test connection").click()
    expect(page.locator(".jirarow .msg.ok")).to_contain_text("Connected as Mia Owner")


def test_the_editor_offers_the_picker_after_a_good_token_check_and_ticking_fills_the_query(page, brain, site):
    with running_ui(brain) as ui:
        open_jira_source(page, ui)
        expect(page.locator(".jirarow .jpick")).to_have_count(0)                                       # not before the check passes
        page.locator(".jirarow").get_by_role("button", name="Choose projects").click()
        page.locator(".jirarow").get_by_label("Operations (OPS)").check()
        expect(page.locator('[data-bind="jira-jql"]')).to_have_value("project in (OPS) ORDER BY updated DESC")
        expect(page.locator(".jirarow .jpick-line")).to_have_text("Every issue in OPS, newest change first.")
        page.locator("#f-name").fill("opsp")
        expect(page.locator("#pv")).to_contain_text("OPS-1")


def test_the_editor_asks_before_replacing_a_query_the_person_wrote(page, brain, site):
    with running_ui(brain) as ui:
        open_jira_source(page, ui, jql="project = HR")
        page.locator(".jirarow").get_by_role("button", name="Choose projects").click()
        page.locator(".jirarow").get_by_label("Operations (OPS)").check()
        expect(page.locator(".jirarow .jpick-replace")).to_contain_text("replace the query you wrote")
        page.locator(".jirarow").get_by_role("button", name="Keep my query").click()
        expect(page.locator('[data-bind="jira-jql"]')).to_have_value("project = HR")
        page.locator(".jirarow").get_by_label("Operations (OPS)").check()
        page.locator(".jirarow").get_by_role("button", name="Replace my query").click()
        expect(page.locator('[data-bind="jira-jql"]')).to_have_value("project in (OPS) ORDER BY updated DESC")


def test_the_editor_picker_is_calm_without_the_scope(page, brain, site):
    site.fail("GET", "/project/search", status=403, times=9)
    with running_ui(brain) as ui:
        open_jira_source(page, ui, jql="project = HR")
        page.locator(".jirarow").get_by_role("button", name="Choose projects").click()
        expect(page.locator(".jirarow .jpick")).to_contain_text("read:jira-work")
        expect(page.locator(".jirarow .jpick [role=alert]")).to_have_count(0)
        expect(page.locator('[data-bind="jira-jql"]')).to_have_value("project = HR")
