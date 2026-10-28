"""MD-103 in a real browser: the On/Off switch on each source row, the preview of an Off source, 'all Off' refused, home and view mode."""
import shutil

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import jira_api  # noqa: E402
from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source  # noqa: E402
from test_ui_browser import browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def world(brain, add_pipeline, make_source, monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    docs = make_source("docs", {f"d{i}.md": f"doc {i}" for i in range(6)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    yield type("W", (), {"brain": brain, "docs": docs, "fake": f})
    jira_api.set_transport(None)


def edit(page, name="p"):
    page.locator("li.prow", has_text=name).get_by_role("button", name="Edit").click()


def test_each_source_row_has_a_switch_and_an_off_row_is_inactive_but_editable(page, world):
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        edit(page)
        folder_switch = page.get_by_role("switch", name="Source docs")
        expect(folder_switch).to_be_checked()
        folder_switch.uncheck()
        expect(page.locator("#w-src0")).to_have_class(__import__("re").compile(r"\boff\b"))
        expect(page.locator("#w-src0")).to_contain_text("Off")
        expect(page.locator("#f-src0")).to_be_enabled()                       # an Off row keeps its settings and can be edited
        expect(page.locator("#f-src0")).to_have_value(str(world.docs))


def test_the_preview_shows_an_off_source_as_kept_and_nothing_is_deleted(page, world):
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        edit(page)
        page.get_by_role("switch", name="Source docs").uncheck()
        expect(page.locator("#pv")).to_contain_text("source off: kept")
        expect(page.locator("#pv")).to_contain_text("Off")
        expect(page.locator("#pv .counts")).to_contain_text("0 delete")
        expect(page.locator("#pv .bad")).to_have_count(0)                     # not shown as an unreadable folder


def test_every_source_off_cannot_be_saved(page, world):
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        edit(page)
        page.get_by_role("switch", name="Source docs").uncheck()
        page.get_by_role("switch", name="Source md-jira").uncheck()
        expect(page.locator("#m-sources")).to_contain_text("Turn a source on, or pause the pipeline.")
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        page.get_by_role("switch", name="Source md-jira").check()
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()


def test_saving_keeps_the_switch_and_home_and_view_mode_show_it(page, world):
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        edit(page)
        page.get_by_role("switch", name="Source docs").uncheck()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("p saved")
        expect(page.locator("li.prow", has_text="p")).to_contain_text("1 of 2 sources off")
        shutil.rmtree(world.docs)                                              # while Off the missing folder is nobody's business
        page.goto(ui.base + "#/pipeline/p")
        expect(page.locator('#view [data-source="docs"] [data-off]')).to_have_text("Off")
        expect(page.locator('#view [data-source="docs"]')).not_to_contain_text("not found")
        expect(page.locator('#view [data-problem]')).to_have_count(0)
    assert [s.enabled for s in load_config(world.brain).get("p").sources] == [False, True]


def test_switching_a_source_back_on_shows_what_changed_while_it_was_off_before_saving(page, world):
    cfg = load_config(world.brain)
    cfg.get("p").sources[0].enabled = False
    from mdpipeline.config import save_config

    save_config(world.brain, cfg)
    (world.docs / "d5.md").unlink()
    (world.docs / "brand-new.md").write_text("new")
    with running_ui(world.brain) as ui:
        open_app(page, ui)
        edit(page)
        expect(page.locator("#pv")).to_contain_text("source off: kept")
        page.get_by_role("switch", name="Source docs").check()
        expect(page.locator("#pv .counts")).to_contain_text("1 delete")
        expect(page.locator("#pv .counts")).to_contain_text("1 create")
        assert (world.brain.root / "raw/p/docs/d5.md").exists()                 # only a preview: nothing happened yet
