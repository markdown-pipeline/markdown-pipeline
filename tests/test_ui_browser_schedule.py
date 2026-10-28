"""MD-121 in a real browser: Needs you at the top of Home, last/next sync with Change, the first-turn-on words, Settings → Background sync."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, notices, scheduler  # noqa: E402
from mdpipeline.config import Pipeline, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def one(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    return brain


def fail_twice(brain):
    for _ in range(2):
        ActivityLog(brain).append({"type": "run", "pipeline": "acme", "trigger": "schedule", "result": "error", "counts": {}, "conflicts": [],
                                   "errors": [{"source": "a", "error": "boom", "category": "network"}]})


def test_needs_you_is_the_first_thing_on_home_and_leads_with_safety(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    fail_twice(one)
    notices.check(one)
    with running_ui(one) as ui:
        open_app(page, ui)
        card = page.locator("#needs-you")
        expect(card).to_be_visible()
        text = card.inner_text()
        assert "couldn't sync the last 2 times" in text and "Nothing was deleted" in text and "boom" not in text
        assert page.locator("#main > *").first.get_attribute("id") == "needs-you"
        shot(page, "schedule-needs-you")


def test_no_needs_you_block_when_all_is_well(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    with running_ui(one) as ui:
        open_app(page, ui)
        expect(page.locator("#needs-you")).to_have_count(0)


def test_a_row_shows_the_next_sync_and_the_interval_can_be_changed(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    with running_ui(one) as ui:
        open_app(page, ui)
        row = page.locator('[data-pipeline="acme"]')
        expect(row.locator("[data-next]")).to_contain_text("Next sync:")
        expect(row.locator("[data-next]")).to_contain_text("every hour")
        row.locator('select[data-schedule="acme"]').select_option("6h")
        expect(row.locator("[data-next]")).to_contain_text("every 6 hours")
        shot(page, "schedule-row")
    assert load_config(one).get("acme").schedule == "6h"


def test_off_and_not_yet_synced_are_said_plainly(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        row = page.locator('[data-pipeline="acme"]')
        expect(row.locator("[data-next]")).to_contain_text("after its first sync")
        row.locator('select[data-schedule="acme"]').select_option("off")
        expect(row.locator("[data-next]")).to_contain_text("Next sync: off")


def test_the_first_sync_says_in_plain_words_that_it_keeps_running_and_it_can_be_turned_off(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Sync now").first.click()
        banner = page.locator("#bg-announce")
        expect(banner).to_be_visible()
        assert "keeps running when this window is closed" in banner.inner_text() and "starts at login" in banner.inner_text()
        shot(page, "schedule-announcement")
        banner.get_by_role("button", name="Turn off").click()
        expect(banner).to_have_count(0)
    assert scheduler.background_enabled() is False


def test_ok_dismisses_the_words_and_they_do_not_come_back(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Sync now").first.click()
        page.locator("#bg-announce").get_by_role("button", name="OK").click()
        expect(page.locator("#bg-announce")).to_have_count(0)
        page.get_by_role("button", name="Sync now").first.click()
        page.wait_for_timeout(400)
        expect(page.locator("#bg-announce")).to_have_count(0)
    assert scheduler.background_enabled() is True


def test_change_in_the_words_acknowledges_them_and_puts_focus_on_the_interval_choice(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Sync now").first.click()
        page.locator("#bg-announce").get_by_role("button", name="Change").click()
        expect(page.locator("#bg-announce")).to_have_count(0)
        expect(page.locator('select[data-schedule="acme"]')).to_be_focused()
        page.reload()
        expect(page.locator("#bg-announce")).to_have_count(0)


def test_the_announcement_is_still_there_after_a_reload_until_the_person_answers(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Sync now").first.click()
        expect(page.locator("#bg-announce")).to_be_visible()
        page.reload()
        expect(page.locator("#bg-announce")).to_be_visible()
        page.locator("#bg-announce").get_by_role("button", name="OK").click()
        expect(page.locator("#bg-announce")).to_have_count(0)
        expect(page.get_by_role("heading", name="Pipelines")).to_be_focused()
        page.reload()
        expect(page.locator("#bg-announce")).to_have_count(0)


def test_a_guard_waiting_for_a_decision_is_amber_not_a_red_failure(page, add_pipeline, make_source, brain):
    src = make_source("g", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("acme", [str(src)]))
    sync_pipeline(brain, "acme", trigger="cli")
    for f in list(src.glob("*.md"))[:8]:
        f.unlink()
    sync_pipeline(brain, "acme", trigger="schedule")
    notices.check(brain)
    with running_ui(brain) as ui:
        open_app(page, ui)
        card = page.locator('[data-notice="guard_blocked"]')
        expect(card).to_contain_text("acme is waiting for your OK")
        expect(card).to_contain_text("nothing was changed yet")
        expect(card.get_by_role("button", name="Review changes")).to_be_visible()
        assert "warn" in card.get_attribute("class") and "✕" not in card.inner_text()
        shot(page, "schedule-guard-waiting")


def test_needs_you_cards_are_announced_to_screen_readers(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    fail_twice(one)
    notices.check(one)
    with running_ui(one) as ui:
        open_app(page, ui)
        assert page.locator('[data-notice="sync_failed"]').get_attribute("role") == "alert"


def test_a_background_process_that_is_not_running_is_said_quietly_with_a_way_to_start_it(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    with running_ui(one) as ui:
        open_app(page, ui)
        line = page.locator("#bg-not-running")
        expect(line).to_contain_text("not running")
        expect(line.get_by_role("button", name="Start it")).to_be_visible()
        scheduler.set_background(False)
        page.reload()
        expect(page.locator("#bg-not-running")).to_have_count(0)        # off by choice is not a problem


def test_the_controls_are_comfortable_targets_and_the_select_has_an_accessible_name_with_the_pipeline(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    fail_twice(one)
    notices.check(one)
    with running_ui(one) as ui:
        open_app(page, ui)
        sel = page.locator('select[data-schedule="acme"]')
        assert sel.bounding_box()["height"] >= 40
        assert page.locator('[data-notice="sync_failed"]').get_by_role("button", name="Try now").bounding_box()["height"] >= 40
        assert page.get_by_role("combobox", name="Sync automatically (acme)").count() == 1


def test_changing_the_interval_keeps_focus_on_the_choice(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    with running_ui(one) as ui:
        open_app(page, ui)
        sel = page.locator('select[data-schedule="acme"]')
        sel.select_option("6h")
        expect(page.locator("[data-next]")).to_contain_text("every 6 hours")
        expect(page.locator('select[data-schedule="acme"]')).to_be_focused()


def test_stepping_through_the_choices_with_the_arrow_keys_saves_the_one_you_stop_on(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    with running_ui(one) as ui:
        open_app(page, ui)
        sel = page.locator('select[data-schedule="acme"]')
        sel.focus()
        for _ in range(2):
            page.keyboard.press("ArrowDown")                 # 1h -> 6h -> daily, quickly
        expect(page.locator("[data-next]")).to_contain_text("every day", timeout=5000)
    assert load_config(one).get("acme").schedule == "daily"


def test_on_a_phone_the_interval_choice_sits_under_the_row_not_in_the_narrow_column(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    page.set_viewport_size({"width": 390, "height": 800})
    with running_ui(one) as ui:
        open_app(page, ui)
        row = page.locator('[data-pipeline="acme"]').bounding_box()
        sel = page.locator('select[data-schedule="acme"]').bounding_box()
        assert sel["x"] >= row["x"] and sel["x"] + sel["width"] <= row["x"] + row["width"] + 1
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_settings_has_a_background_sync_switch(page, one):
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Settings").click()
        card = page.locator("#bg-settings")
        expect(card).to_contain_text("Keep syncing when this window is closed (starts when you log in to your desktop)")
        expect(card.get_by_role("button", name="On")).to_have_attribute("aria-pressed", "true")
        card.get_by_role("button", name="Off").click()
        expect(card.get_by_role("button", name="Off")).to_have_attribute("aria-pressed", "true")
        expect(card).to_contain_text("Nothing syncs by itself. Use Sync now, or turn this on.")
        shot(page, "schedule-settings")
        card.get_by_role("button", name="On").click()
        expect(card.get_by_role("button", name="On")).to_have_attribute("aria-pressed", "true")
    assert scheduler.background_enabled() is True


def test_view_mode_shows_the_next_sync(page, one):
    sync_pipeline(one, "acme", trigger="cli")
    with running_ui(one) as ui:
        open_app(page, ui)
        page.get_by_role("link", name="acme").click()
        expect(page.locator("#v-next")).to_contain_text("every hour")
