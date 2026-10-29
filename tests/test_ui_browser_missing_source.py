"""MD-136 in a real browser: a missing source folder is a calm notice with Choose new location / Remove this source, never a red error."""
import shutil

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline.config import Pipeline, Source, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_ui_browser import browser, open_activity, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def gone(brain, add_pipeline, make_source):
    a = make_source("A", {"a1.md": "# a1\n", "a2.md": "# a2\n"})
    b = make_source("B", {"b1.md": "# b1\n"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "A"), Source(str(b), "B")], target="{relpath}", name_policy="original"))
    sync_pipeline(brain, "p")
    shutil.rmtree(a)
    sync_pipeline(brain, "p")
    return a


def test_view_mode_shows_a_calm_notice_and_no_red_error(page, brain, gone):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        need = page.locator('[data-need="source_missing"]').first
        expect(need).to_contain_text("Source folder not found")
        expect(need).to_contain_text("Nothing was deleted; your files are kept")
        expect(need).to_have_class(__import__("re").compile(r"\bwarn\b"))
        expect(page.locator(".banner.err, .vneed.err, .derr")).to_have_count(0)
        shot(page, "md136-view-calm")


@pytest.mark.flaky(reruns=2, reason="MD-198: about half of the CI runs leave the 'Source folder not found' card on screen after the new folder is saved (never locally); cause not found, product accepted a bounded rerun on 2026-10-29")
def test_choose_new_location_refuses_a_bad_path_then_saves_a_good_one_and_shows_the_plan(page, brain, gone, tmp_path):
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / "a1.md").write_text("# a1\n")
    (moved / "a2.md").write_text("# a2\n")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        page.get_by_role("button", name="Choose new location").first.click()
        box = page.locator("input[data-msrc-path]")
        box.fill("/nonexistent/nowhere")
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator(".msrc-form .derr")).to_contain_text("Folder not found")
        assert load_config(brain).get("p").sources[0].path == str(gone)
        page.locator("input[data-msrc-path]").fill(str(moved))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#toast")).to_contain_text("new location saved")
        # the notice clears when the next-sync dry run has been made; on a slow CI runner that takes longer than the default 5 s (public CI, 2026-10-29)
        try:
            expect(page.locator('[data-need="source_missing"]')).to_have_count(0, timeout=30000)
        except AssertionError:
            # MD-198: what the page and the server say when the card stays (read in the CI log; the cause is not known yet)
            state = page.evaluate("JSON.stringify({screen: S.screen, hash: location.hash, msrc: S.msrc, busy: S.busy, version: S.version, "
                                  "needs: S.view && S.view.data && S.view.data.needs_you && S.view.data.needs_you.map(n => n.kind + ':' + n.path), "
                                  "viewName: S.view && S.view.name, planProblems: S.view && S.view.plan && S.view.plan.problems && S.view.plan.problems.map(p => p.kind), "
                                  "toast: document.querySelector('#toast') && document.querySelector('#toast').innerText})")
            server = ui.get("/api/pipelines/p/view")[1]["needs_you"]
            raise AssertionError(f"card stayed. page={state} server_needs={[(n['kind'], n.get('path')) for n in server]} moved={moved}")
        expect(page.locator("#planned-at")).to_be_visible(timeout=30000)
    assert load_config(brain).get("p").sources[0].path == str(moved)


def test_remove_this_source_keeps_the_files(page, brain, gone):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        page.get_by_role("button", name="Remove this source").first.click()
        expect(page.locator("#toast")).to_contain_text("2 files kept")
    assert [s.label for s in load_config(brain).get("p").sources] == ["B"]
    assert (brain.root / "a1.md").is_file()


def test_home_shows_the_missing_source_as_an_amber_needs_you_card_that_opens_the_pipeline(page, brain, gone, monkeypatch):
    from mdpipeline import notices

    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)
    notices.check(brain)
    with running_ui(brain) as ui:
        open_app(page, ui)
        card = page.locator('[data-notice="source_missing"]')
        expect(card).to_have_class(__import__("re").compile(r"\bwarn\b"))
        expect(card).to_contain_text("Nothing was deleted")
        card.get_by_role("button", name="Fix the source").click()
        expect(page.locator('[data-need="source_missing"]').first).to_be_visible()


# MD-135: the summary at the top of view mode -----------------------------------------------------------------------------------
def test_view_mode_starts_with_the_summary_and_the_next_sync_line(page, brain, add_pipeline, make_source):
    src = make_source("notes", {"a.md": "# a\n", "b.md": "# b\n"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "notes")], target="raw/{relpath}"))
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        what = page.locator("#v-about-what")
        expect(what).to_contain_text("Turns ")
        expect(what).to_contain_text("under raw/. Checks for changes every hour.")
        expect(page.locator("#v-about-next")).to_have_text("Not synced yet. Press Sync now to write 2 files; after that it checks every hour.")
        assert page.locator("#view > section").first.get_attribute("id") == "v-about"       # the first panel
        shot(page, "md135-summary")
        sync_pipeline(brain, "p")
        page.get_by_role("button", name="Refresh").click()
        expect(page.locator("#v-about-next")).to_have_text("Next sync: nothing to change.")


def test_a_missing_folder_replaces_the_second_line_with_the_needs_you_text(page, brain, gone):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        expect(page.locator("#v-about-next")).to_have_text("Needs you: Source folder not found.")


def test_a_missing_source_is_amber_everywhere_never_a_red_error_in_view_mode_home_or_activity(page, brain, gone):
    """MD-136 AC3 (product, 2026-10-15): red is for real failures. A missing source shows 'needs you' in the badge, status, plan row and run history."""
    import re

    red = ".pill.error, tr.grp.bad, .vneed.err, .banner.err, .errline, .derr"
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        expect(page.locator('[data-need="source_missing"]').first).to_be_visible()
        expect(page.locator("#planned-at")).to_be_visible()
        main = page.locator("#main")
        expect(page.locator(red)).to_have_count(0)
        expect(main).not_to_contain_text(re.compile(r"\berror\b", re.I))
        expect(main.locator(".bar .pill")).to_have_text("needs you")
        expect(page.locator("#v-summary")).to_contain_text("needs you")
        expect(page.locator("#v-summary")).to_contain_text("needs you: source folder not found")
        expect(page.locator("#v-last")).to_contain_text("stopped, source folder missing; nothing changed")
        expect(page.locator("tr.grp.warn")).to_contain_text("Nothing was deleted")
        shot(page, "md136-view-amber")
        page.evaluate("location.hash = '#/home'")
        expect(page.locator(".pill.error")).to_have_count(0)
        expect(page.locator(".errline")).to_have_count(0)
        open_activity(page)
        expect(page.locator(".pill.error")).to_have_count(0)
        expect(page.locator("#main .pill.needs").first).to_have_text("needs you")


def test_the_activity_tab_is_found_while_a_see_all_in_activity_button_is_on_screen(page, brain, gone):
    """MD-169: the flaky 'Activity' click. The click ran right after the hash change, while the pipeline page (with its 'See all in Activity'
    button) was still on screen, so the substring role match found two buttons. With the old click this test fails every time."""
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate("location.hash = '#/pipeline/p'")
        expect(page.get_by_role("button", name="See all in Activity")).to_be_visible()
        open_activity(page)
        expect(page.get_by_role("navigation", name="Screens").locator('[data-screen="activity"]')).to_have_attribute("aria-current", "page")
