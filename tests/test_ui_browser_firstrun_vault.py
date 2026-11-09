"""MD-163 in a real browser: a machine with no OS keychain. One calm line on Connect says where the token is saved; there is no passphrase step.
Review asks only for a v0.1.1-v0.1.3 passphrase vault (once) or tells how to fix an unsafe secrets file."""
import json
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_ui_browser_firstrun import jira, quiet  # noqa: E402,F401
from test_jira_sync import EMAIL, TOKEN  # noqa: E402
from uihelper import running_ui  # noqa: E402
from vaultperm import WINDOWS, user_only, widen  # noqa: E402


@pytest.fixture
def no_keychain(app_data):
    app_data.keychain.available = False
    return app_data


def fill_jira(page):
    page.get_by_role("button", name="Jira Cloud").click()
    page.get_by_label("Jira site").fill("acme")
    page.get_by_label("Your Atlassian email").fill(EMAIL)
    page.get_by_label("API token").fill(TOKEN)


def to_review(page, tmp_path):
    page.get_by_role("button", name="Test and continue").click()
    page.get_by_role("button", name=re.compile("^Continue with")).click()
    page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "o"))
    page.get_by_role("button", name="Use this folder").click()
    expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()


NOTE = "This computer has no system keychain, so your {} is kept in a file only your user can read, the way `gh` and the AWS CLI do it. To use a keychain instead, install libsecret-tools."


def test_without_a_keychain_connect_says_once_where_the_token_is_saved_and_has_no_passphrase_field(page, brain, jira, no_keychain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.locator("#fr-store-note")).to_have_text(NOTE.format("token"))
        expect(page.locator("#fr-vault")).to_have_count(0)
        expect(page.locator("#fr-vpass")).to_have_count(0)
        shot(page, "firstrun-store-note-connect")


def test_with_a_keychain_there_is_no_passphrase_note_or_field_anywhere(page, brain, jira, app_data, tmp_path):
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.locator("#fr-store-note")).to_have_count(0)
        to_review(page, tmp_path)
        expect(page.locator("#fr-vault")).to_have_count(0)
        page.get_by_role("button", name="Write 6 files").click()
        expect(page.locator("#fr-ready")).to_contain_text("6 files in")


def test_an_old_passphrase_vault_shows_one_unlock_field_on_review_and_the_write_continues(page, brain, jira, no_keychain, tmp_path):
    from test_secrets_file import legacy_vault
    from mdpipeline import vault

    legacy_vault(no_keychain, "correct horse")
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        to_review(page, tmp_path)
        expect(page.locator("#fr-vault")).to_contain_text("Enter your passphrase")
        expect(page.locator("#fr-vconfirm")).to_have_count(0)
        page.locator("#fr-vpass").fill("wrong")
        page.get_by_role("button", name=re.compile("^Write")).click()
        expect(page.locator("#fr-error")).to_be_visible()
        page.locator("#fr-vpass").fill("correct horse")
        page.get_by_role("button", name=re.compile("^Write")).click()
        expect(page.locator("#fr-ready")).to_contain_text("6 files in")
    vault.reset_session()
    assert vault.default_vault().describe()["protection"] == "file"          # migrated: no passphrase needed from now on


def test_an_unsafe_secrets_file_is_explained_on_review_and_checking_again_continues(page, brain, jira, no_keychain, tmp_path):
    from mdpipeline import vault

    vault.default_vault().add("jira-token", "old", "x")
    path = no_keychain.dir / "vault.json"
    widen(path)
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        to_review(page, tmp_path)
        expect(page.locator("#fr-vault")).to_contain_text("PowerShell" if WINDOWS else f"chmod 600 {path}")
        page.get_by_role("button", name=re.compile("^Write")).click()
        expect(page.locator("#fr-error")).to_be_visible()                        # still unsafe: nothing written
        user_only(path, directory=False)
        page.get_by_role("button", name=re.compile("^Write")).click()
        expect(page.locator("#fr-ready")).to_contain_text("6 files in")


def test_the_review_step_reports_secret_store_used_once_with_only_the_store(page, brain, jira, no_keychain, tmp_path):
    from mdpipeline.usage import UsageStore
    from test_ui_browser_usage_events import own, wait_for

    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        to_review(page, tmp_path)
        page.get_by_role("button", name=re.compile("^Write")).click()
        events = wait_for("secret_store_used")
    assert [own(e) for e in events] == [{"store": "file"}]


# MD-215 (AC8 guard, secrets): a forced redraw puts a typed token / passphrase back in its field and makes NO new copy of it anywhere ----------------
INSTRUMENT = """
window.__tracked = [];
const __t = window.track;
window.track = (event, properties) => { window.__tracked.push(JSON.stringify([event, properties])); return __t(event, properties); };
"""


def _copies_in_page_state(page, secret):
    """How many times the secret appears in the page's own first-run state (FR). The typed value is held there by design (the input handler), so the
    guarantee is that a redraw adds no further copy, not that the state is empty."""
    return page.evaluate("(s) => JSON.stringify(FR).split(s).length - 1", secret)


def _nowhere_else(page, requests, since, secret):
    assert page.evaluate("(s) => JSON.stringify([localStorage, sessionStorage, document.cookie]).includes(s)", secret) is False, "the secret reached browser storage"
    assert not [r for r in requests[since:] if secret in (r.post_data or "") or secret in r.url], "the secret was sent during the redraw"
    assert page.evaluate("(s) => window.__tracked.some((t) => t.includes(s))", secret) is False, "the secret reached a track call"


def test_a_redraw_puts_a_typed_token_and_passphrase_back_and_copies_them_nowhere(page, brain, jira, no_keychain, tmp_path):
    from test_secrets_file import legacy_vault
    from mdpipeline import vault

    passphrase = "correct horse battery staple"
    legacy_vault(no_keychain, passphrase)
    requests = []
    page.on("request", lambda r: requests.append(r))
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.evaluate(INSTRUMENT)
        fill_jira(page)                                                              # the token is typed on the Connect step
        page.wait_for_timeout(700)                                                   # the debounced progress save has gone out (it never includes the token)
        before, mark = _copies_in_page_state(page, TOKEN), len(requests)
        page.evaluate("renderFirstRun(); renderFirstRun()")                          # forced redraws, twice
        assert page.get_by_label("API token").input_value() == TOKEN
        assert _copies_in_page_state(page, TOKEN) == before, "a redraw added a copy of the token to the page state"
        _nowhere_else(page, requests, mark, TOKEN)

        to_review(page, tmp_path)
        page.locator("#fr-vpass").fill(passphrase)                                   # the passphrase is typed on Review
        page.wait_for_timeout(700)
        before, mark = _copies_in_page_state(page, passphrase), len(requests)
        page.evaluate("renderFirstRun(); renderFirstRun()")
        assert page.locator("#fr-vpass").input_value() == passphrase
        focus = page.evaluate("document.activeElement.id")                           # DS-P-1106-2: focus is a RECORDED diagnostic here, not an assert (the strict focus coverage is
        print(f"MD-215 diagnostic: after the redraws on step 'review' activeElement.id={focus!r}")      # test_the_passphrase_keeps_focus_and_value_through_every_forced_redraw)
        if focus != "fr-vpass":
            import warnings
            warnings.warn(f"MD-215 diagnostic: on step 'review' the passphrase field did not hold focus after the redraws; activeElement.id={focus!r}")
        assert _copies_in_page_state(page, passphrase) == before, "a redraw added a copy of the passphrase to the page state"
        _nowhere_else(page, requests, mark, passphrase)
    vault.reset_session()


# MD-215 (DS-P-1106-1): where is the focus lost on the Review step? Hold the review reply, type the passphrase, release the reply, force redraws -- assert after EVERY step -------------
def _focus_state(page):
    return page.evaluate("({id: document.activeElement.id, tag: document.activeElement.tagName, value: (document.getElementById('fr-vpass') || {}).value})")


def _assert_in_passphrase(page, step, passphrase):
    state = _focus_state(page)
    assert state["id"] == "fr-vpass" and state["value"] == passphrase, f"FOCUS/VALUE LOST AT STEP '{step}': activeElement={state}"


def _to_typed_passphrase(page, passphrase):
    page.wait_for_function("document.querySelector('#fr-vpass')")
    page.wait_for_timeout(300)                                                           # the step's own focus move (heading, 0 ms timer) has long fired
    page.locator("#fr-vpass").fill(passphrase)
    _assert_in_passphrase(page, "1 after fill", passphrase)
    page.wait_for_timeout(700)
    _assert_in_passphrase(page, "2 after 700 ms", passphrase)


def test_the_passphrase_keeps_focus_and_value_through_every_forced_redraw(page, brain, jira, no_keychain, tmp_path):
    """The passphrase field exists only once the review reply is in (while it counts, Review shows a progress card), so the person types after the reply.
    Fill, then assert focus and value after the fill, after the wait, and after EACH forced redraw: the failure message names the first step that lost them."""
    from test_secrets_file import legacy_vault
    from mdpipeline import vault

    passphrase = "correct horse battery staple"
    legacy_vault(no_keychain, passphrase)
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        to_review(page, tmp_path)
        _to_typed_passphrase(page, passphrase)
        page.evaluate("renderFirstRun()")
        _assert_in_passphrase(page, "3 after redraw 1", passphrase)
        page.evaluate("renderFirstRun()")
        _assert_in_passphrase(page, "4 after redraw 2", passphrase)
    vault.reset_session()


def test_the_passphrase_keeps_focus_and_value_when_a_fresh_review_reply_arrives_after_typing(page, brain, jira, no_keychain, tmp_path):
    """A reply can arrive after the typing only through a FRESH read (frLoadReview(true)); nothing on the Review step starts one (only typing in the scope
    JQL does). The reply is held, then released, with the focus/value asserted after each step."""
    from test_secrets_file import legacy_vault
    from mdpipeline import vault

    passphrase = "correct horse battery staple"
    legacy_vault(no_keychain, passphrase)
    held, hold = [], {"on": False}
    page.route("**/api/first-run/review", lambda route: held.append(route) if hold["on"] else route.continue_())
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        to_review(page, tmp_path)
        _to_typed_passphrase(page, passphrase)
        hold["on"] = True
        page.evaluate("() => { frLoadReview(true); }")                                   # a fresh read starts; its reply is held
        page.wait_for_function("() => FR.reviewing === true")
        assert held, "the fresh review request was not held"
        during = _focus_state(page)                                                      # recorded, not asserted: Review shows the counting card, there is no field to focus
        hold["on"] = False
        for route in held:
            route.continue_()
        page.wait_for_function("() => FR.review !== null && FR.reviewing === false")
        _assert_in_passphrase(page, f"3 after the fresh reply arrived (while held: {during})", passphrase)
        page.evaluate("renderFirstRun()")
        _assert_in_passphrase(page, "4 after redraw 1", passphrase)
    vault.reset_session()
