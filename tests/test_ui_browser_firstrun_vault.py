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
