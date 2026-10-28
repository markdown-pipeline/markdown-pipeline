"""MD-137: wherever the app says the GitHub sign-in expired, a 'Sign in again' button runs the device flow in place (no editor round trip)."""
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import notices  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_release_gate_view_mode import break_it, world  # noqa: E402,F401
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

NAME = "gh_jira"


@pytest.fixture
def expired(world, monkeypatch):
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)
    break_it(world, "sign-in-expired", monkeypatch)
    assert sync_pipeline(world.brain, NAME, trigger="ui").category == "sign-in-expired"
    return world


def go_view(page):
    page.evaluate(f"location.hash = '#/pipeline/{NAME}'")


def test_the_home_row_data_says_the_sign_in_is_needed(expired):
    with running_ui(expired.brain) as ui:
        _, state = ui.get("/api/state")
    rows = {r["name"]: r for r in state["pipelines"]}
    assert rows[NAME]["needs_signin"] is True
    assert not any(r["needs_signin"] for n, r in rows.items() if n != NAME)


def test_view_mode_signs_in_again_in_place_and_offers_sync_now(page, expired):
    with running_ui(expired.brain) as ui:
        open_app(page, ui)
        go_view(page)
        need = page.locator('[data-need="target"]').first
        expect(need).to_contain_text("Nothing was written or deleted")
        need.get_by_role("button", name="Sign in again").click()
        flow = page.locator("[data-reauth]")
        expect(flow).to_contain_text("github.com/login/device")
        expect(flow.locator("[data-reauth-code]")).to_have_text(re.compile(r".+"))
        expired.gh.authorize_device()
        expect(page.locator("[data-reauth-done]")).to_contain_text("Signed in again as", timeout=20000)
        expect(page.locator("[data-reauth-done]")).to_contain_text("Nothing was deleted")
        assert page.evaluate("location.hash") == f"#/pipeline/{NAME}"            # never left view mode: no editor, no unsaved-change prompt
        page.locator("[data-reauth-done]").get_by_role("button", name="Sync now").click()
        expect(page.locator("#toast")).to_contain_text("synced", timeout=20000)
        expect(page.locator('[data-need="target"]')).to_have_count(0)               # the notice cleared
    assert sync_pipeline(expired.brain, NAME, trigger="cli").result == "ok"


def test_home_shows_the_button_on_the_needs_you_card(page, expired):
    sync_pipeline(expired.brain, NAME, trigger="ui")
    notices.check(expired.brain)
    with running_ui(expired.brain) as ui:
        open_app(page, ui)
        card = page.locator(f'[data-notice][data-pipeline="{NAME}"]')
        expect(card.get_by_role("button", name="Sign in again")).to_be_visible()


def test_activity_card_has_the_button_too(page, expired):
    with running_ui(expired.brain) as ui:
        open_app(page, ui, screen="activity")
        expect(page.locator("article.ecard").get_by_role("button", name="Sign in again")).to_be_visible()


def test_a_missing_installation_shows_the_install_link_and_saves_nothing(page, expired):
    expired.gh._installed.clear()                                  # the app is not installed on the repository any more
    with running_ui(expired.brain) as ui:
        open_app(page, ui)
        go_view(page)
        page.locator('[data-need="target"]').first.get_by_role("button", name="Sign in again").click()
        expired.gh.authorize_device()
        missing = page.locator("[data-reauth-missing]")
        expect(missing).to_contain_text("not installed on", timeout=20000)
        expect(missing.get_by_role("link", name="Install it on that repository")).to_be_visible()
        expired.gh.install_app(["acme/brain"])
        missing.get_by_role("button", name="check again").click()
        expect(page.locator("[data-reauth-done]")).to_contain_text("Signed in again as", timeout=20000)
