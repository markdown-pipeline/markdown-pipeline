"""MD-159 in a real browser: Projects -> Where should your files go? -> Review (confirmation only) -> Ready, for the three paths."""
import json
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline.config import load_config  # noqa: E402
from test_jira_sync import EMAIL, TOKEN  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_ui_browser_firstrun import jira, notes, quiet  # noqa: E402,F401
from test_ui_browser_firstrun_github import SLOW, fake  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402
from vaultperm import is_user_only  # noqa: E402

WRITE = re.compile(r"^(Write|Commit) ")


def fill_jira(page):
    page.get_by_role("button", name="Jira Cloud").click()
    page.get_by_label("Jira site").fill("acme")
    page.get_by_label("Your Atlassian email").fill(EMAIL)
    page.get_by_label("API token").fill(TOKEN)
    page.get_by_role("button", name="Test and continue").click()


def folder_to_target(page, ui, notes):
    open_app(page, ui)
    page.get_by_role("button", name="Folder on this computer").click()
    page.get_by_label("Folder path").fill(str(notes))
    expect(page.locator("#fr-folder-msg")).to_contain_text("Folder found")
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()


def steps(page):
    return [t.strip() for t in page.locator("ol.fr-steps li").all_inner_texts()]


# --- AC1: the step order and the stepper --------------------------------------------------------------------------------
def test_the_folder_path_has_where_files_go_between_choose_folder_and_review(page, brain, notes):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        assert [s.removeprefix("✓ ") for s in steps(page)] == ["Source", "Choose folder", "Where files go", "Review", "Ready"]
        expect(page.locator("[aria-current=step]")).to_have_text("Where files go")


def test_the_jira_path_has_projects_then_where_files_go_then_review(page, brain, jira):
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.get_by_role("heading", name="Which Jira issues should become files?")).to_be_visible()
        assert [s.removeprefix("✓ ") for s in steps(page)] == ["Source", "Connect Jira", "Projects", "Where files go", "Review", "Ready"]


# --- AC4 / AC13: two equal cards, nothing preselected, no way to write before a choice ------------------------------------
def test_where_files_go_has_two_cards_nothing_chosen_and_no_write_or_continue_button(page, brain, notes):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        group = page.get_by_role("group", name="Where the files go")
        expect(group.get_by_role("heading", name="A folder on this computer")).to_be_visible()
        expect(group.get_by_role("heading", name="Your team's GitHub repo")).to_be_visible()
        expect(page.locator("input[type=radio]:checked")).to_have_count(0)
        expect(page.get_by_role("button", name=WRITE)).to_have_count(0)
        expect(page.get_by_role("button", name="Continue", exact=True)).to_have_count(0)
        expect(page.get_by_role("textbox", name="Output folder")).to_have_value(re.compile(r"^~/markdown-pipeline/team-wiki$"))
        expect(page.locator("#fr-dest-msg")).to_contain_text("New folder")
        assert load_config(brain).pipelines == []
        shot(page, "firstrun-flow-where-files-go")


def test_the_folder_card_checks_the_typed_folder_live(page, brain, notes, tmp_path):
    (tmp_path / "exists").mkdir()
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        field = page.get_by_role("textbox", name="Output folder")
        field.fill(str(tmp_path / "exists"))
        expect(page.locator("#fr-dest-msg")).to_contain_text("This folder exists. We add our files next to yours and never change yours.")
        field.fill(str(blocker / "sub"))
        expect(page.locator("#fr-dest-msg")).to_contain_text("We can't write there")
        expect(page.get_by_role("button", name="Use this folder")).to_be_disabled()
        field.fill(str(tmp_path / "fresh"))
        expect(page.locator("#fr-dest-msg")).to_contain_text("New folder. We create it when you press Write.")
        expect(page.get_by_role("button", name="Use this folder")).to_be_enabled()


def test_the_github_card_is_visible_and_disabled_with_the_reason_when_sign_in_is_not_set_up(page, brain, notes):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        card = page.locator("[data-card=github]")
        expect(card).to_be_visible()
        expect(card).to_have_attribute("aria-disabled", "true")
        expect(card).to_contain_text("Not available in this build: GitHub sign-in isn't set up. Use a folder now; you can add a repo later in Advanced setup.")
        expect(card.get_by_role("button", name="Sign in with GitHub")).to_be_disabled()


def test_card_b_copy_is_marketings_c2_c3_c4(page, brain, notes, fake):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        card = page.locator("[data-card=github]")
        expect(card).to_contain_text("Your team's agents read the same files. Changes arrive as one commit per sync, in a repo you choose.")
        expect(card).to_contain_text("You approve the app on github.com with a short code, then choose which repos it can use. No token to create or paste.")
        expect(card).to_contain_text("GitHub sign-in")
        expect(card).not_to_contain_text("About 1 min")


# --- AC3 / AC4 / AC5: folder -> folder to written files; Review is confirmation only ------------------------------------------
def test_folder_to_folder_goes_to_written_files_with_a_review_that_only_confirms(page, brain, notes, tmp_path):
    dest = tmp_path / "out"
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        page.get_by_role("textbox", name="Output folder").fill(str(dest))
        expect(page.locator("#fr-dest-msg")).to_contain_text("New folder")
        page.get_by_role("button", name="Use this folder").click()
        expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()
        summary = page.locator("#fr-summary")
        expect(summary).to_contain_text("7 files → 7 Markdown files")
        expect(summary.locator("[data-row=from]")).to_contain_text("team-wiki")
        expect(summary.locator("[data-row=from]")).to_contain_text("Read-only: we never change anything in this folder.")
        expect(summary.locator("[data-row=to]")).to_contain_text(str(dest))
        expect(summary.locator("[data-row=to]")).to_contain_text("Your output folder")
        expect(summary.locator("[data-row=then]")).to_contain_text("Checks for changes every hour, including deletions.")
        expect(page.get_by_role("heading", name="Preview: what your agent gets")).to_be_visible()
        expect(page.locator("#fr-example")).to_contain_text("source")
        for gone in ("input[type=radio]", "#fr-adv", "#fr-projects", "#fr-dest"):                 # nothing to edit on Review
            expect(page.locator(gone)).to_have_count(0)
        expect(page.locator("#main")).not_to_contain_text("Advanced setup for a GitHub repository")
        expect(page.locator("#fr-vault")).to_have_count(0)                  # P6: no token is stored, so no passphrase (keychain present or not)
        assert load_config(brain).pipelines == [] and not dest.exists()
        page.get_by_role("button", name=f"Write 7 files to {dest}").click()
        expect(page.locator("#fr-ready")).to_contain_text("7 files in")
    assert len(list(dest.rglob("*.md"))) == 7


def test_a_folder_to_folder_first_run_never_asks_for_a_passphrase_without_a_keychain(page, brain, notes, tmp_path, app_data):
    app_data.keychain.available = False
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "out"))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()
        expect(page.locator("#fr-vault")).to_have_count(0)
        page.get_by_role("button", name=WRITE).click()
        expect(page.locator("#fr-ready")).to_contain_text("7 files in")


# --- Jira -> folder -------------------------------------------------------------------------------------------------------
def test_jira_to_folder_projects_then_destination_then_review_then_files(page, brain, jira, tmp_path):
    dest = tmp_path / "jira-out"
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.locator("#fr-count")).to_contain_text("6 issues")                                 # the default is ready
        expect(page.get_by_role("radio", name="All projects you can see")).to_be_checked()
        expect(page.get_by_role("radio", name="90 days")).to_be_checked()
        page.get_by_role("button", name="Continue with 6 issues").click()
        expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()
        expect(page.get_by_role("textbox", name="Output folder")).to_have_value("~/markdown-pipeline/acme-jira")
        page.get_by_role("textbox", name="Output folder").fill(str(dest))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#fr-summary")).to_contain_text("6 Jira issues → 6 Markdown files")
        expect(page.locator("#fr-summary [data-row=from]")).to_contain_text("acme.atlassian.net")
        page.get_by_role("button", name=f"Write 6 files to {dest}").click()
        expect(page.locator("#fr-ready")).to_contain_text("6 files in")
    assert TOKEN not in (brain.config_dir / "config.yaml").read_text()


def test_jira_without_a_keychain_never_asks_for_a_passphrase_and_keeps_the_token_in_the_user_only_file(page, brain, jira, tmp_path, app_data):
    app_data.keychain.available = False
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        page.get_by_role("button", name=re.compile("^Continue with")).click()
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "o"))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#fr-vault")).to_have_count(0)
        expect(page.locator("#fr-vpass")).to_have_count(0)
        page.get_by_role("button", name=WRITE).click()
        expect(page.locator("#fr-ready")).to_contain_text("6 files in")
    vault_file = app_data.dir / "vault.json"
    assert is_user_only(vault_file) and json.loads(vault_file.read_text())["kdf"] == "file"


# --- AC2 / AC3: the Projects step ------------------------------------------------------------------------------------------
def test_the_time_window_buttons_rebuild_the_query_and_recount(page, brain, jira):
    seen = []
    page.on("request", lambda r: seen.append(r.post_data) if r.url.endswith("/api/first-run/review") and r.post_data else None)
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.locator("#fr-count")).to_contain_text("6 issues")
        page.get_by_role("radio", name="30 days").check()
        expect(page.locator("#fr-count")).to_contain_text("issues")
        page.get_by_role("radio", name="All time").check()
        page.wait_for_timeout(800)
    jqls = [json.loads(b).get("jql") for b in seen]
    assert "updated >= -30d ORDER BY updated DESC" in jqls and "ORDER BY updated DESC" in jqls


def test_zero_issues_disables_continue_and_offers_all_time(page, brain, jira):
    def empty(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "ok": True, "pipeline": "acme-jira", "noun": "issues", "count": 0, "first": [], "paths": [], "more": 0, "target": "directory",
            "dest": {"path": "/x", "exists": False}, "read_only": "Read-only: we never change anything in Jira.", "example": None, "jql": "x"}))
    page.route("**/api/first-run/review", empty)
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.locator("#fr-count")).to_contain_text("No issues match")
        expect(page.get_by_role("button", name="Continue with 0 issues")).to_be_disabled()
        expect(page.get_by_role("button", name="Include all time")).to_be_visible()


def test_more_than_2000_items_warns_on_projects_and_on_review_with_a_way_to_narrow(page, brain, jira, tmp_path):
    def big(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "ok": True, "pipeline": "acme-jira", "noun": "issues", "count": 2450, "first": ["a"], "paths": ["a.md"], "more": 2449, "target": "directory",
            "dest": {"path": str(tmp_path / "o"), "exists": False}, "read_only": "Read-only: we never change anything in Jira.", "example": None, "jql": "x"}))
    page.route("**/api/first-run/review", big)
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        expect(page.locator("#fr-large")).to_contain_text("takes a few minutes")
        expect(page.get_by_role("button", name="Only the last 30 days")).to_be_visible()
        page.get_by_role("button", name=re.compile("^Continue with")).click()
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "o"))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#fr-summary")).to_contain_text("takes a few minutes")
        expect(page.get_by_role("button", name="Narrow it")).to_be_visible()


# --- AC5 / AC9: Change goes to the owning step and keeps state ---------------------------------------------------------------
def test_change_on_to_goes_back_to_where_files_go_with_the_choice_kept(page, brain, notes, tmp_path):
    dest = tmp_path / "mine"
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        page.get_by_role("textbox", name="Output folder").fill(str(dest))
        page.get_by_role("button", name="Use this folder").click()
        page.locator("[data-row=to]").get_by_role("button", name="Change").click()
        expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()
        expect(page.get_by_role("textbox", name="Output folder")).to_have_value(str(dest))
        expect(page.locator("[data-card=folder]")).to_contain_text("Your choice")
        expect(page.get_by_role("button", name="Continue with this folder")).to_be_visible()
        assert load_config(brain).pipelines == []


def test_change_on_from_goes_to_choose_folder_and_for_jira_to_projects(page, brain, notes, jira, tmp_path):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        page.get_by_role("button", name="Use this folder").click()
        page.locator("[data-row=from]").get_by_role("button", name="Change").click()
        expect(page.get_by_role("heading", name="Choose a folder")).to_be_visible()
        expect(page.get_by_label("Folder path")).to_have_value(str(notes))
    with running_ui(brain) as ui:
        page.goto("about:blank")
        open_app(page, ui)
        page.get_by_role("button", name="Start over").click() if page.get_by_role("button", name="Start over").count() else None
        page.get_by_role("button", name="Jira Cloud").click()
        page.get_by_label("Jira site").fill("acme")
        page.get_by_label("Your Atlassian email").fill(EMAIL)
        page.get_by_label("API token").fill(TOKEN)
        page.get_by_role("button", name="Test and continue").click()
        page.get_by_role("radio", name="30 days").check()
        page.get_by_role("button", name=re.compile("^Continue with")).click()
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "j"))
        page.get_by_role("button", name="Use this folder").click()
        page.locator("[data-row=from]").get_by_role("button", name="Change").click()
        expect(page.get_by_role("heading", name="Which Jira issues should become files?")).to_be_visible()
        expect(page.get_by_role("radio", name="30 days")).to_be_checked()


# --- GitHub ---------------------------------------------------------------------------------------------------------------
def test_folder_to_github_signs_in_in_the_card_uses_the_repo_and_commits(page, brain, notes, fake):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        head = fake.head()
        page.locator("[data-card=github]").get_by_role("button", name="Sign in with GitHub").click()
        expect(page.locator("#fr-gh-code")).to_have_text("WDJB-MJHT")
        fake.authorize_device()
        expect(page.locator("#fr-gh")).to_contain_text("Signed in as", timeout=SLOW)
        use = page.get_by_role("button", name="Use this repo")
        expect(use).to_be_enabled(timeout=SLOW)
        assert fake.head() == head and load_config(brain).pipelines == []
        use.click()
        expect(page.locator("#fr-summary [data-row=to]")).to_contain_text("acme/team-docs")
        expect(page.locator("#fr-summary [data-row=to]")).to_contain_text("Your GitHub repo")
        expect(page.locator("#fr-summary [data-row=then]")).to_contain_text("commits them")
        assert fake.head() == head
        page.get_by_role("button", name="Commit 7 files to acme/team-docs").click()
        expect(page.locator("#fr-ready")).to_contain_text("7 files in acme/team-docs", timeout=SLOW)
    assert load_config(brain).get("team-wiki").destination["repo"] == "acme/team-docs"


def test_jira_to_github_with_sign_in_mocked_commits_the_issues(page, brain, jira, fake):
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        page.get_by_role("button", name=re.compile("^Continue with")).click()
        page.locator("[data-card=github]").get_by_role("button", name="Sign in with GitHub").click()
        expect(page.locator("#fr-gh-code")).to_be_visible()
        fake.authorize_device()
        expect(page.get_by_role("button", name="Use this repo")).to_be_enabled(timeout=SLOW)
        page.get_by_role("button", name="Use this repo").click()
        page.get_by_role("button", name="Commit 6 files to acme/team-docs").click()
        expect(page.locator("#fr-ready")).to_contain_text("6 files in acme/team-docs", timeout=SLOW)
    assert load_config(brain).get("acme-jira").destination["type"] == "github"


# --- AC8: an empty folder is caught where it can be fixed ---------------------------------------------------------------------
def test_an_empty_folder_blocks_continue_on_choose_folder(page, brain, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Folder on this computer").click()
        page.get_by_label("Folder path").fill(str(empty))
        expect(page.locator("#fr-folder-msg")).to_contain_text("Folder found, but it has no Markdown or text files yet. Choose a folder that has some.")
        expect(page.get_by_role("button", name="Continue")).to_be_disabled()


# --- AC11: usage ---------------------------------------------------------------------------------------------------------------
def test_the_steps_report_scope_then_target_with_dest_changed_then_preview_and_nothing_identifying(page, brain, jira, tmp_path):
    from mdpipeline.usage import UsageStore
    from test_ui_browser_usage_events import own, queued, wait_for

    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        open_app(page, ui)
        fill_jira(page)
        page.get_by_role("button", name=re.compile("^Continue with")).click()
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "secret-place"))
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#fr-summary")).to_be_visible()
        wait_for("onboarding_step_completed", 3)
        entries = queued("onboarding_step_viewed", "onboarding_step_completed")
    viewed = [own(e)["step"] for e in entries if e["event"] == "onboarding_step_viewed"]
    done = {own(e)["step"]: own(e) for e in entries if e["event"] == "onboarding_step_completed"}
    assert viewed.index("choose_scope") < viewed.index("choose_target") < viewed.index("preview")
    assert done["choose_target"]["target"] == "directory" and done["choose_target"]["dest_changed"] is True
    assert "target" not in done.get("choose_scope", {}) and "secret-place" not in json.dumps(entries)


# --- AC12: resume ---------------------------------------------------------------------------------------------------------------
def test_resume_returns_a_folder_run_to_where_files_go(page, brain, notes):
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        page.wait_for_timeout(700)                                                          # the step is saved after 400 ms
        page.reload()
        expect(page.locator("#fr-resume")).to_contain_text("Where files go")
        page.get_by_role("button", name="Continue where I stopped").click()
        expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()


# --- AC10: accessibility, phone width --------------------------------------------------------------------------------------------
def test_focus_goes_to_the_heading_and_the_page_never_scrolls_sideways_at_phone_width(page, brain, notes, tmp_path):
    page.set_viewport_size({"width": 390, "height": 800})
    with running_ui(brain) as ui:
        folder_to_target(page, ui, notes)
        expect(page.locator("#main h1")).to_be_focused()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        height = page.get_by_role("button", name="Use this folder").bounding_box()["height"]
        assert height >= 40
        shot(page, "firstrun-flow-where-files-go-390")
        page.get_by_role("button", name="Use this folder").click()
        expect(page.locator("#fr-summary")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        shot(page, "firstrun-flow-review-390")
