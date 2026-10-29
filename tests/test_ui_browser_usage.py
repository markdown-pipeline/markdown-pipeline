"""MD-115 in a real browser: the consent card (AC1) and Settings → Privacy & usage (AC3, AC5)."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import usage  # noqa: E402
from mdpipeline.usage import UsageStore  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


def card(page):
    return page.locator("#usage-card")


def open_settings(page):
    page.get_by_role("button", name="Settings").click()
    expect(page.get_by_role("heading", name="Privacy & usage")).to_be_visible()


def test_a_new_install_sees_the_consent_card_with_two_equal_unselected_buttons(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(card(page)).to_be_visible()
        share, decline = card(page).get_by_role("button", name="Share usage"), card(page).get_by_role("button", name="Don't share")
        expect(share).to_be_visible()
        expect(decline).to_be_visible()
        assert share.get_attribute("class") == decline.get_attribute("class")          # equal style: neither is "the good one"
        assert share.bounding_box()["width"] == pytest.approx(decline.bounding_box()["width"], abs=24)
        assert share.get_attribute("aria-pressed") is None and decline.get_attribute("aria-pressed") is None
        text = card(page).inner_text()
        assert "Never your content, titles, file names, paths, queries or tokens" in text
        assert "If you don't choose, nothing is shared" in text
        shot(page, "usage-card")
    assert UsageStore().consent == "unanswered"


def test_share_collapses_the_card_to_one_line_and_is_stored(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        card(page).get_by_role("button", name="Share usage").click()
        expect(card(page)).to_contain_text("Sharing anonymous usage")
        expect(card(page)).to_contain_text("Settings → Privacy & usage")
        expect(card(page).get_by_role("button", name="Share usage")).to_have_count(0)
        shot(page, "usage-card-collapsed")
    assert UsageStore().sharing is True


def test_dont_share_collapses_the_card_and_is_stored(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        card(page).get_by_role("button", name="Don't share").click()
        expect(card(page)).to_contain_text("Not sharing")
    assert UsageStore().consent == "decline"


def test_undo_brings_the_question_back_and_clears_the_choice(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        card(page).get_by_role("button", name="Share usage").click()
        card(page).get_by_role("button", name="Undo").click()
        expect(card(page).get_by_role("button", name="Don't share")).to_be_visible()
    assert UsageStore().consent == "unanswered"


def test_carrying_on_without_answering_counts_no_answer_once_and_never_blocks(page, brain, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().set_counter_enabled(True)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        expect(page.locator("#f-name")).to_be_visible()                                  # the editor opened: the card did not block
        page.get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Advanced setup").first.click()
        expect(page.locator("#f-name")).to_be_visible()
    assert [e["properties"]["outcome"] for e in transport.events] == ["no_answer"]
    assert UsageStore().sharing is False


def test_no_card_once_the_user_has_chosen(page, brain):
    UsageStore().set_consent("decline")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(card(page)).to_have_count(0)


def test_settings_for_a_new_install_says_nothing_is_shared_and_shows_dont_share_as_current(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#usage-settings")).to_contain_text("You haven't chosen yet, so nothing is shared.")
        expect(page.get_by_role("button", name="Don't share")).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role("button", name="Share usage")).to_have_attribute("aria-pressed", "false")
        for line in usage.WHAT_WE_SEND + usage.NEVER_SENT:
            expect(page.locator("#usage-settings")).to_contain_text(line)
        expect(page.locator("#usage-settings")).to_contain_text("Sent only when you press Send, even with sharing off")
        expect(page.locator("#usage-settings")).to_contain_text("mdpipeline usage off")
        expect(page.locator("#usage-settings")).to_contain_text(UsageStore().install_id)
        shot(page, "usage-settings")


def test_the_toggle_is_effective_at_once_both_ways(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        page.get_by_role("button", name="Share usage").click()
        expect(page.get_by_role("button", name="Share usage")).to_have_attribute("aria-pressed", "true")
        assert UsageStore().sharing is True
        page.get_by_role("button", name="Don't share").click()
        expect(page.get_by_role("button", name="Don't share")).to_have_attribute("aria-pressed", "true")
        assert UsageStore().sharing is False
        expect(page.locator("#usage-settings")).to_contain_text("Nothing is sent while sharing is off")


def test_the_live_log_shows_what_was_sent_newest_first_exactly_as_sent(page, brain):
    store = UsageStore()
    store.set_consent("share")
    store.record("first_sync_completed", {"source_type": "jira", "ms_since_first_open": 4100})
    store.flush(Recorder())
    store.send_user_chosen("source_requested", {"source_id": "notion"}, Recorder())
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        rows = page.locator("#usage-log tbody tr")
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text("source_requested")
        expect(rows.nth(0)).to_contain_text("goes to the team")
        expect(rows.nth(1)).to_contain_text("first_sync_completed")
        expect(rows.nth(1)).to_contain_text('{"source_type": "jira", "ms_since_first_open": 4100}')
        shot(page, "usage-settings-log")


def test_reset_replaces_the_install_id_shown_and_stored(page, brain):
    old = UsageStore().install_id
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        page.get_by_role("button", name="Reset").click()
        expect(page.locator("#usage-settings")).not_to_contain_text(old)
    assert UsageStore().install_id != old
    expect_new = UsageStore().install_id
    assert expect_new


def test_see_exactly_what_we_send_opens_the_two_lists_an_example_and_the_same_two_buttons(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_text("See exactly what we send").click()
        drawer = page.locator("#usage-drawer")
        expect(drawer).to_be_visible()
        for line in usage.WHAT_WE_SEND + usage.NEVER_SENT:
            expect(drawer).to_contain_text(line)
        expect(drawer).to_contain_text("Example")
        expect(drawer.get_by_role("button", name="Share usage")).to_be_visible()
        expect(drawer.get_by_role("button", name="Don't share")).to_be_visible()
        drawer.get_by_role("button", name="Don't share").click()
    assert UsageStore().consent == "decline"


@pytest.mark.parametrize("where", ["home", "settings"])
def test_no_horizontal_scroll_on_a_phone(browser, brain, where):
    ctx = browser.new_context(viewport={"width": 375, "height": 800})
    pg = ctx.new_page()
    try:
        with running_ui(brain) as ui:
            open_app(pg, ui)
            if where == "settings":
                open_settings(pg)
            overflow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 0
    finally:
        ctx.close()


def test_settings_names_the_uvx_command_for_a_uvx_or_registry_install(page, brain, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "registry")
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_settings(page)
        expect(page.locator("#usage-settings")).to_contain_text("uvx mdpipeline usage off")
