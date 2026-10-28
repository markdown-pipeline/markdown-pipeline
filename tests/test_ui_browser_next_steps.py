"""MD-147 in a real browser: ordinary (non-error) states name the next step, and never point at something that is not there."""
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, notices  # noqa: E402
from mdpipeline.config import Pipeline, Source  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_agents import bin_dir, calls, install  # noqa: E402,F401
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_ui_browser_jira_picker import quiet as _quiet_jira, site, to_review  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

APP_JS = (Path(__file__).parent.parent / "src" / "mdpipeline" / "ui_static" / "app.js").read_text()


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def never_synced(brain, add_pipeline, make_source):
    src = make_source("notes", {"a.md": "# a\n", "b.md": "# b\n"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "notes")], target="raw/{relpath}", name_policy="original"))
    return src


@pytest.fixture
def synced_twice(brain, add_pipeline, make_source):
    src = make_source("notes", {"a.md": "# a\n", "b.md": "# b\n"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "notes")], target="raw/{relpath}", name_policy="original"))
    sync_pipeline(brain, "p")
    (src / "c.md").write_text("# c\n")
    sync_pipeline(brain, "p")
    return src


# 1. Advanced setup opens calm ------------------------------------------------------------------------------------------

def test_advanced_setup_opens_with_no_red_errors_and_says_how_to_start(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        expect(page.locator("#m-sources")).to_contain_text("Start by adding a source: a folder on this computer or a Jira site")
        expect(page.get_by_text("+ Add source")).to_be_visible()
        expect(page.locator(".editor .msg.err, .editor .invalid, .editor .banner.err")).to_have_count(0)
        expect(page.locator("#acts .why")).to_contain_text("Fix")          # the "Fix N fields above to save" line stays


def test_the_name_check_shows_once_the_name_field_was_touched(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        page.locator("#f-name").fill("x y")
        page.locator("#f-name").fill("")
        expect(page.locator("#m-name .msg.err")).to_have_count(1)


# 2. A never-synced pipeline in view mode --------------------------------------------------------------------------------

def test_view_mode_of_a_never_synced_pipeline_says_to_press_sync_now(page, brain, never_synced):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        expect(page.locator("#v-about-next")).to_have_text("Not synced yet. Press Sync now to write 2 files; after that it checks every hour.")
        expect(page.locator("#view-mode-tag")).to_be_visible()
        expect(page.locator(".vrow", has_text="Status")).not_to_contain_text("never synced")
        expect(page.get_by_role("heading", name="No sync has run yet")).to_be_visible()
        expect(page.get_by_role("heading", name="Last 0 runs")).to_have_count(0)
        expect(page.get_by_role("button", name="Sync now")).to_have_class("btn primary")
        expect(page.get_by_role("button", name="Edit")).not_to_have_class("btn primary")


def test_a_never_synced_pipeline_with_the_schedule_off_says_only_when_you_sync_it(page, brain, add_pipeline, make_source):
    src = make_source("notes", {"a.md": "# a\n"})
    pipeline = Pipeline.new("p", [Source(str(src), "notes")], target="raw/{relpath}", name_policy="original")
    pipeline.schedule = "off"
    add_pipeline(pipeline)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        expect(page.locator("#v-about-next")).to_have_text("Not synced yet. Press Sync now to write 1 file; after that it runs only when you sync it.")


def test_a_synced_pipeline_keeps_the_normal_next_sync_line_and_edit_stays_primary(page, brain, synced_twice):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        expect(page.locator("#v-about-next")).not_to_contain_text("Not synced yet")
        expect(page.get_by_role("button", name="Sync now")).not_to_have_class("btn primary")
        expect(page.get_by_role("button", name="Edit")).to_have_class("btn primary")


# 3. The Home agent hint ---------------------------------------------------------------------------------------------------

def test_home_offers_connect_when_no_agent_is_connected_and_hides_the_ask_your_agent_hint(page, brain, synced_twice, bin_dir):
    install(bin_dir, "claude")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("li.prow")).to_be_visible()
        expect(page.get_by_text("Your connected agent can answer this")).to_have_count(0)
        expect(page.get_by_role("button", name="Connect Claude Code")).to_be_visible()


def test_home_shows_connected_with_disconnect_and_the_hint_once_an_agent_is_connected(page, brain, synced_twice, bin_dir):
    install(bin_dir, "claude")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Connect Claude Code").click()
        expect(page.locator("#ag-panel")).to_contain_text("Claude Code connected")
        expect(page.get_by_role("button", name="Disconnect")).to_be_visible()
        expect(page.get_by_text("Your connected agent can answer this")).to_be_visible()
        expect(page.locator(".mcp .snip")).to_be_hidden()      # the raw command lives only under "Another agent"


# 4. The Jira project picker hint ------------------------------------------------------------------------------------------

def test_the_first_run_projects_step_makes_all_projects_an_explicit_default_choice(page, brain, site):
    """MD-159 replaced the "leave all unticked" hint: the default is a visible, selected option, and "Only some" with nothing ticked says what to do."""
    from test_ui_browser_jira_picker import some_projects, to_scope

    with running_ui(brain) as ui:
        to_scope(page, ui)
        expect(page.get_by_role("radio", name="All projects you can see")).to_be_checked()
        some_projects(page)
        expect(page.locator("#fr-projects-hint")).to_contain_text("Tick at least one project, or choose All projects.")


def test_the_editor_picker_carries_the_same_hint():
    assert APP_JS.count("Leave all unticked to include every project you can see.") == 1


def test_switching_the_manual_agent_tab_does_not_fold_another_agent(page, brain, synced_twice, bin_dir):
    install(bin_dir, "claude")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#ag-panel")).to_be_visible()
        page.get_by_text("Another agent (Cursor, …)").click()
        page.get_by_role("button", name="Codex", exact=True).click()
        expect(page.locator(".ag-other")).to_have_attribute("open", "")
        expect(page.locator("#snip")).to_contain_text("mcp_servers.mdpipeline")
