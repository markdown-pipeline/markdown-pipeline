"""MD-122 in a real browser: "Last sync: 1 new · 1 changed · 1 deleted" on Home and in view mode, the item list on click, the agent hint."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from conftest import write  # noqa: E402
from mdpipeline import autostart, notices  # noqa: E402
from mdpipeline.config import Pipeline, Source  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_agents import bin_dir, calls, install  # noqa: E402,F401
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def synced(brain, add_pipeline, make_source):
    src = make_source("docs", {"alpha.md": "# A\n", "beta.md": "# B\n", "gamma.md": "# G\n"})
    add_pipeline(Pipeline.new("acme", [Source(str(src), "docs")], name_policy="original"))
    sync_pipeline(brain, "acme")
    write(src / "alpha.md", "# A2\n")
    write(src / "delta.md", "# D\n")
    (src / "gamma.md").unlink()
    sync_pipeline(brain, "acme")
    return brain


def test_home_shows_the_last_sync_line_and_expands_to_the_titles(page, synced):
    with running_ui(synced) as ui:
        open_app(page, ui)
        row = page.locator("li.prow[data-pipeline=acme]")
        expect(row.locator(".changes-line")).to_contain_text("Last sync: 1 new · 1 changed · 1 deleted")
        expect(row.locator(".changes-list")).to_have_count(0)
        row.get_by_role("button", name="Show").click()
        expect(row.locator(".changes-list")).to_contain_text("delta")
        expect(row.locator(".changes-list")).to_contain_text("alpha")
        expect(row.locator(".changes-list")).to_contain_text("gamma")
        shot(page, "changes-home")
        row.get_by_role("button", name="Hide").click()
        expect(row.locator(".changes-list")).to_have_count(0)


def test_home_hints_at_the_question_to_ask_the_agent_once_an_agent_is_connected(page, synced, bin_dir):
    install(bin_dir, "claude")
    with running_ui(synced) as ui:
        open_app(page, ui)
        expect(page.locator("#main")).not_to_contain_text("Ask it: what changed since yesterday")       # MD-147: nothing to ask yet
        page.get_by_role("button", name="Connect Claude Code").click()
        expect(page.locator("#main")).to_contain_text("Ask it: what changed since yesterday")


def test_a_pipeline_that_never_synced_has_no_last_sync_line_and_no_hint(page, brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("fresh", [str(make_source("f", {"x.md": "x"}))]))
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator(".changes-line")).to_have_count(0)
        expect(page.locator("#main")).not_to_contain_text("Ask it: what changed since yesterday")


def test_view_mode_shows_the_same_line_and_list(page, synced):
    with running_ui(synced) as ui:
        open_app(page, ui)
        page.locator("li.prow[data-pipeline=acme] .name a").click()
        expect(page.locator("#v-changes")).to_contain_text("1 new · 1 changed · 1 deleted")
        page.locator("#v-changes").get_by_role("button", name="Show").click()
        expect(page.locator("#v-changes .changes-list")).to_contain_text("delta")
        shot(page, "changes-view")
