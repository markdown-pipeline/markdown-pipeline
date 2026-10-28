"""MD-92 in a real browser: view mode opens from the home, has its own URL, shows the whole configuration read-only."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def two(brain, add_pipeline, make_source):
    a = make_source("raw", {"1.md": "one", "2.md": "two"})
    b = make_source("wiki", {"index.md": "idx"})
    add_pipeline(Pipeline.new("biz", [Source(str(a), "raw"), Source(str(b), "wiki")], name_policy="date-name", guard_threshold_percent=30))
    add_pipeline(Pipeline.new("solo", [str(make_source("single", {"s.md": "s"}))]))
    return brain


def open_view(page, name="biz"):
    page.locator("li.prow", has_text=name).get_by_role("link", name=name).click()
    expect(page.locator("#view")).to_be_visible()


def test_clicking_a_pipeline_opens_view_mode_with_its_own_url_and_reload_reopens_it(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        assert page.url.endswith("#/pipeline/biz")
        expect(page.locator("h1")).to_contain_text("biz")
        page.reload()
        expect(page.locator("#view")).to_be_visible()
        expect(page.locator("h1")).to_contain_text("biz")
        page.go_back()
        expect(page.locator("li.prow", has_text="biz")).to_be_visible()


def test_view_mode_shows_the_whole_configuration(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        v = page.locator("#view")
        expect(v).to_contain_text("Folder found · 2 .md files")
        expect(v).to_contain_text("Folder found · 1 .md file")
        expect(v).to_contain_text("raw/{pipeline}/{source}/{relpath}")
        expect(v).to_contain_text("Date + name")
        expect(v).to_contain_text("Example:")
        expect(v).to_contain_text("**/*.md")
        expect(v).to_contain_text("30% of the files")
        expect(v).to_contain_text("delete the copy")
        expect(v).to_contain_text(str(two.root))


def test_view_mode_has_no_editable_field(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        expect(page.locator("#view input, #view textarea, #view select")).to_have_count(0)
        expect(page.locator("#view-mode-tag")).to_be_visible()


def test_an_unknown_pipeline_url_says_not_found_with_a_link_home(page, two):
    with running_ui(two) as ui:
        page.goto(ui.base + "#/pipeline/nope")
        expect(page.locator("#view-notfound")).to_contain_text("Pipeline not found")
        page.get_by_role("button", name="Back to the pipelines").click()
        expect(page.locator("li.prow", has_text="biz")).to_be_visible()


def test_a_missing_folder_is_shown_plainly(page, two, tmp_path):
    (tmp_path / "sources" / "wiki").rename(tmp_path / "moved")
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        expect(page.locator('[data-source="wiki"]')).to_contain_text("Folder not found")


def test_edit_opens_the_editor_and_cancel_returns_to_view_mode(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Edit", exact=True).click()
        expect(page.locator("#f-name")).to_have_value("biz")
        page.get_by_role("button", name="Cancel").click()
        expect(page.locator("#view")).to_be_visible()
        assert page.url.endswith("#/pipeline/biz")


def test_save_in_the_editor_returns_to_view_mode_with_the_new_value(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Edit", exact=True).click()
        page.get_by_text("Advanced").click()
        page.locator("#f-guard").fill("55")
        expect(page.locator("#acts")).to_contain_text("Save")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#view")).to_be_visible()
        expect(page.locator("#view")).to_contain_text("55% of the files")
    assert load_config(two).get("biz").guard_threshold_percent == 55


def test_sync_pause_and_back_from_view_mode(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        page.get_by_role("button", name="Sync now").click()
        expect(page.locator("#toast")).to_contain_text("biz synced")
        assert ActivityLog(two).read(types={"run"})[-1]["trigger"] == "ui"
        page.get_by_role("button", name="Pause").click()
        expect(page.locator("#view")).to_contain_text("Paused")
        expect(page.get_by_role("button", name="Sync now")).to_be_disabled()
        page.get_by_role("button", name="Resume").click()
        expect(page.get_by_role("button", name="Sync now")).to_be_enabled()
        page.get_by_role("button", name="Back to home").click()
        expect(page.locator("li.prow", has_text="biz")).to_be_visible()
        assert "pipeline" not in page.url


# ---- MD-93: the next-sync dry run in view mode ----------------------------------------------------------------------
def plan_requests(page):
    seen = []
    page.on("request", lambda r: seen.append(r.method) if r.url.split("?")[0].endswith("/plan") else None)
    return seen


def test_the_dry_run_runs_on_open_shows_counts_planned_at_and_refresh_runs_it_again(page, two, tmp_path):
    seen = plan_requests(page)
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        plan = page.locator("#view-plan")
        expect(plan).to_contain_text("Next sync (dry run)")
        expect(plan.locator(".counts")).to_contain_text("3 all")
        expect(plan.locator("table")).to_contain_text("_1.md")
        expect(page.locator("#planned-at")).to_have_text(__import__("re").compile(r"\d\d:\d\d"))
        assert seen == ["GET"]
        (tmp_path / "sources" / "raw" / "3.md").write_text("three")
        page.get_by_role("button", name="Refresh").click()
        expect(plan.locator(".counts")).to_contain_text("4 all")
        assert seen == ["GET", "GET"]


def test_opening_and_refreshing_the_view_writes_nothing(page, two, tmp_path, app_data):
    roots = [two.root, tmp_path / "sources"]
    before = __import__("conftest").tree_snapshot(*roots)
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        expect(page.locator("#view-plan table")).to_be_visible()
        page.get_by_role("button", name="Refresh").click()
        expect(page.locator("#view-plan table")).to_be_visible()
    assert __import__("conftest").tree_snapshot(*roots) == before


def test_a_missing_folder_shows_the_problem_and_no_deletes(page, two, tmp_path):
    from mdpipeline.engine import sync_pipeline

    sync_pipeline(two, "biz")
    (tmp_path / "sources" / "wiki").rename(tmp_path / "moved")
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        problem = page.locator('[data-need="source_missing"]').first                # MD-136: calm notice with two ways forward
        expect(problem).to_contain_text("Source folder not found")
        expect(problem).to_contain_text("Nothing was deleted; your files are kept")
        expect(page.locator("#view-plan .counts")).to_contain_text("0 delete")


def test_a_paused_pipeline_shows_its_plan_and_cannot_sync(page, two):
    from mdpipeline.service import set_paused

    set_paused(two, "biz", True, trigger="cli")
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        expect(page.locator("#view-plan .counts")).to_contain_text("3 all")
        expect(page.get_by_role("button", name="Sync now")).to_be_disabled()


def test_the_guard_prediction_points_to_needs_you(page, brain, add_pipeline, make_source):
    from mdpipeline.engine import sync_pipeline

    src = make_source("docs", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("big", [str(src)]))
    sync_pipeline(brain, "big")
    for f in list(src.glob("*.md"))[:6]:
        f.unlink()
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_view(page, "big")
        banner = page.locator('#view-plan [data-banner="guard"]')
        expect(banner).to_contain_text("It will stop at the guard")
        expect(banner).to_contain_text("Needs you")


def test_sync_now_from_view_updates_the_plan(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page)
        expect(page.locator("#view-plan .counts")).to_contain_text("3 create")
        page.get_by_role("button", name="Sync now").click()
        expect(page.locator("#toast")).to_contain_text("biz synced")
        expect(page.locator("#view-plan .counts")).to_contain_text("0 create")
        expect(page.locator("#view-plan .counts")).to_contain_text("3 skip")


def test_a_jira_source_shows_connected_after_the_dry_run(page, brain, add_pipeline, monkeypatch):
    from fake_jira import FakeJira
    from mdpipeline import jira_api
    from test_jira_sync import EMAIL, TOKEN, jira_source

    f = FakeJira(site="acme", email=EMAIL, token=TOKEN)
    f.add_issue("MD", "one")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    add_pipeline(Pipeline("j", [jira_source()], name_policy="date-name"))
    try:
        with running_ui(brain) as ui:
            open_app(page, ui)
            open_view(page, "j")
            expect(page.locator('[data-source="jira"]')).to_contain_text("Jira: connected")
            monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG")
            page.get_by_role("button", name="Refresh").click()
            expect(page.locator('[data-source="jira"]')).to_contain_text("rejected")
            expect(page.locator('#view-plan [data-problem="source"]')).to_contain_text("Jira rejected the token")
            assert "ATATT-WRONG" not in page.content()
    finally:
        jira_api.set_transport(None)


# ---- MD-94: status, Needs you and the last 10 runs ---------------------------------------------------------------------
def test_status_last_sync_owned_files_and_empty_states(page, two):
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page, "solo")
        expect(page.locator("#v-last")).to_have_text("never synced")
        expect(page.locator("#v-noneed")).to_be_visible()
        expect(page.locator("#v-noruns")).to_be_visible()
        page.get_by_role("button", name="Sync now").click()
        expect(page.locator("#v-summary")).to_have_text("1 created")
        expect(page.locator("#v-owned")).to_have_text("1")
        expect(page.locator("#v-last")).to_contain_text("last run ok by UI")


def test_the_last_ten_runs_expand_to_file_changes_and_link_to_activity_filtered_to_the_pipeline(page, two, tmp_path):
    from mdpipeline.engine import sync_pipeline

    for i in range(12):
        (tmp_path / "sources" / "single" / "s.md").write_text(f"v{i}")
        sync_pipeline(two, "solo", trigger="cli")
    sync_pipeline(two, "biz", trigger="cli")
    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page, "solo")
        rows = page.locator("#view-runs tr.r")
        expect(rows).to_have_count(10)
        expect(rows.first).to_contain_text("CLI")
        rows.first.click()
        expect(page.locator("#view-runs tr.files").first).to_contain_text("s.md")
        page.get_by_role("button", name="See all in Activity").click()
        expect(page.locator("main .bar")).to_contain_text("Filtered to pipeline solo")
        expect(page.locator("table.runs tbody tr.r").first).to_be_visible()
        names = {t.strip() for t in page.locator("table.runs tbody tr.r td:nth-child(2)").all_inner_texts()}
        assert names == {"solo"}
        page.get_by_role("button", name="Show all").click()
        expect(page.locator("main .bar")).to_have_count(0)


def test_needs_you_items_of_this_pipeline_link_to_activity(page, brain, add_pipeline, make_source):
    from mdpipeline.engine import sync_pipeline

    src = make_source("docs", {f"n{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("big", [str(src)]))
    sync_pipeline(brain, "big")
    for f in list(src.glob("*.md"))[:6]:
        f.unlink()
    sync_pipeline(brain, "big", trigger="mcp")
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_view(page, "big")
        need = page.locator('[data-need="guard"]')
        expect(need).to_contain_text("would delete or overwrite 6 of 10")
        expect(page.get_by_role("button", name="Approve")).to_have_count(0)      # approval stays in Activity
        need.get_by_role("button", name="Handle in Activity").click()
        expect(page.get_by_role("button", name="Approve & sync")).to_be_visible()


def test_the_view_updates_without_a_reload_when_a_sync_runs_elsewhere(page, two, tmp_path):
    from mdpipeline.engine import sync_pipeline

    with running_ui(two) as ui:
        open_app(page, ui)
        open_view(page, "solo")
        page.evaluate("window.__still_here = true")
        expect(page.locator("#v-noruns")).to_be_visible()
        sync_pipeline(two, "solo", trigger="mcp")                 # a sync from another trigger
        expect(page.locator("#view-runs tr.r")).to_have_count(1, timeout=8000)
        expect(page.locator("#view-runs tr.r").first).to_contain_text("MCP")
        expect(page.locator("#v-owned")).to_have_text("1")
        assert page.evaluate("window.__still_here") is True


def test_a_github_pipeline_shows_its_last_commit_link(page, brain, add_pipeline, make_source, monkeypatch):
    from fake_github import FakeGitHub
    from mdpipeline import github_api
    from mdpipeline.engine import sync_pipeline
    from mdpipeline.targetconfig import GitHubSpec

    monkeypatch.setenv("BRAIN_TOKEN", "ghp_BROWSERVIEWSECRET0123")
    fake = FakeGitHub(repo="acme/brain", branch="main", token="ghp_BROWSERVIEWSECRET0123", files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    try:
        add_pipeline(Pipeline.new("g", [str(make_source("docs", {"a.md": "A"}))], destination=GitHubSpec("acme/brain", "env:BRAIN_TOKEN", "main").to_dict()))
        sync_pipeline(brain, "g")
        with running_ui(brain) as ui:
            open_app(page, ui)
            open_view(page, "g")
            link = page.locator("#view a[href*='github.com/acme/brain/commit/']")
            expect(link).to_be_visible()
            assert "ghp_BROWSERVIEW" not in page.content()
    finally:
        github_api.set_transport(None)
