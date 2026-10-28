"""MD-118 in a real browser: Start -> (Connect Jira | Choose folder) -> Review -> Ready, with the click count of each path (AC8)."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import autostart, jira_api, notices  # noqa: E402
from mdpipeline.config import Pipeline, load_config  # noqa: E402
from test_jira_sync import EMAIL, TOKEN  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def notes(make_source):
    return make_source("team-wiki", {f"n{i}.md": f"# Note {i}\n" for i in range(7)})


@pytest.fixture
def jira():
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(6):
        f.add_issue("ACME", f"Issue number {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    yield f
    jira_api.set_transport(None)


class Clicks:
    """Counts the clicks of a path: AC8 asks Dev to report the clicks from open to first file."""

    def __init__(self, page):
        self.n = 0
        self.page = page

    def click(self, locator):
        locator.click()
        self.n += 1


def test_empty_brain_opens_on_start_with_two_sources_and_no_help_text(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.get_by_role("heading", name="Connect your first source")).to_be_visible()
        expect(page.get_by_role("button", name="Jira Cloud")).to_contain_text("Needs an API token")
        expect(page.get_by_role("button", name="Folder on this computer")).to_contain_text("No sign-in")
        expect(page.get_by_role("button", name="Advanced setup")).to_be_visible()
        expect(page.locator("[aria-current=step]")).to_have_text("Source")
        shot(page, "firstrun-start")


def test_folder_path_goes_from_start_to_files_in_four_clicks_and_writes_nothing_before_the_last(page, brain, notes, tmp_path):
    dest = tmp_path / "markdown-pipeline" / "team-wiki"
    c = Clicks(page)
    with running_ui(brain) as ui:
        open_app(page, ui)
        c.click(page.get_by_role("button", name="Folder on this computer"))
        page.get_by_label("Folder path").fill(str(notes))
        expect(page.locator("#fr-folder-msg")).to_contain_text("Folder found · 7 Markdown files")
        c.click(page.get_by_role("button", name="Continue"))
        expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()        # MD-159: the destination is asked, not preselected
        page.get_by_role("textbox", name="Output folder").fill(str(dest))
        expect(page.locator("#fr-dest-msg")).to_contain_text("New folder")
        assert not dest.exists() and load_config(brain).pipelines == []
        c.click(page.get_by_role("button", name="Use this folder"))                                  # the choice itself is the one added click
        expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()
        expect(page.locator("#fr-review")).to_contain_text("7 Markdown files")
        expect(page.locator("#fr-review")).to_contain_text("n0.md")
        expect(page.locator("#fr-review")).to_contain_text("and 2 more")
        expect(page.locator("#fr-review")).to_contain_text("Read-only: we never change anything in this folder.")
        expect(page.locator("#fr-example")).to_contain_text("# Note 0")
        shot(page, "firstrun-review")
        assert not dest.exists() and load_config(brain).pipelines == []
        c.click(page.get_by_role("button", name="Write 7 files"))
        expect(page.locator("#fr-ready")).to_contain_text("7 files in")
        expect(page.locator("#fr-ready")).to_contain_text("Stays current: next check at")
        expect(page.locator("#fr-ready")).to_contain_text("then every hour")
        shot(page, "firstrun-ready")
    assert c.n == 4 and len(list(dest.rglob("*.md"))) == 7                      # folder, Continue, Use this folder (the choice), Write


def test_jira_path_connects_reviews_and_writes_in_five_clicks(page, brain, jira, tmp_path):
    c = Clicks(page)
    with running_ui(brain) as ui:
        open_app(page, ui)
        c.click(page.get_by_role("button", name="Jira Cloud"))
        page.get_by_label("Jira site").fill("acme")
        page.get_by_label("Your Atlassian email").fill(EMAIL)
        page.get_by_label("API token").fill(TOKEN)
        shot(page, "firstrun-connect")
        c.click(page.get_by_role("button", name="Test and continue"))
        expect(page.get_by_role("heading", name="Which Jira issues should become files?")).to_be_visible()
        expect(page.locator("#main")).to_contain_text("Connected as Mia Owner")
        c.click(page.get_by_role("button", name="Continue with 6 issues"))
        c.click(page.get_by_role("button", name="Use this folder"))
        expect(page.get_by_role("heading", name="Review before we write anything")).to_be_visible()
        expect(page.locator("#fr-review")).to_contain_text("6 Jira issues")
        expect(page.locator("#fr-review")).to_contain_text("ACME-1 ")
        expect(page.locator("#fr-review")).to_contain_text("Read-only: we never change anything in Jira.")
        c.click(page.get_by_role("button", name="Write 6 files"))
        expect(page.locator("#fr-ready")).to_contain_text("6 files in")
    assert c.n == 5                                                                  # Jira, Test and continue, Continue with 6 issues, Use this folder, Write
    assert TOKEN not in (brain.config_dir / "config.yaml").read_text()


def test_a_bad_token_stays_on_connect_with_one_plain_message_and_writes_nothing(page, brain, jira):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Jira Cloud").click()
        page.get_by_label("Jira site").fill("acme")
        page.get_by_label("Your Atlassian email").fill(EMAIL)
        page.get_by_label("API token").fill("wrong-token")
        page.get_by_role("button", name="Test and continue").click()
        expect(page.locator("#fr-error[role=alert]")).to_be_visible()
        expect(page.get_by_role("heading", name="Connect Jira")).to_be_visible()
        expect(page.locator("#fr-error")).to_contain_text("Atlassian didn't accept this email and token together")
        assert "wrong-token" not in page.locator("#fr-error").inner_text()
    assert load_config(brain).pipelines == []


def test_leaving_and_coming_back_resumes_at_the_last_unfinished_step_without_the_token(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Jira Cloud").click()
        page.get_by_label("Jira site").fill("acme")
        page.get_by_label("Your Atlassian email").fill("dana@acme.dev")
        page.get_by_label("API token").fill("secret-not-kept")
        page.get_by_role("button", name="Back").click()           # moving between steps saves where the person is
        page.get_by_role("button", name="Jira Cloud").click()
        page.reload()
        expect(page.locator("#fr-resume")).to_contain_text("Welcome back")
        expect(page.locator("#fr-resume")).to_contain_text("Connect Jira")
        shot(page, "firstrun-resume")
        page.get_by_role("button", name="Continue where I stopped").click()
        expect(page.get_by_label("Jira site")).to_have_value("acme")
        expect(page.get_by_label("Your Atlassian email")).to_have_value("dana@acme.dev")
        expect(page.get_by_label("API token")).to_have_value("")


def test_start_over_forgets_where_the_person_stopped(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Folder on this computer").click()
        page.get_by_label("Folder path").fill("/somewhere")
        page.wait_for_timeout(700)                                  # typing is remembered a moment after the last key
        page.reload()
        expect(page.locator("#fr-resume")).to_contain_text("Choose a folder")
        page.get_by_role("button", name="Start over").click()
        expect(page.locator("#fr-resume")).to_have_count(0)
        page.reload()
        expect(page.locator("#fr-resume")).to_have_count(0)


def test_advanced_setup_opens_the_existing_editor(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        expect(page.locator("#f-name")).to_be_visible()


def test_existing_pipelines_skip_the_first_run_and_stay_untouched(page, brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.get_by_role("heading", name="Connect your first source")).to_have_count(0)
        expect(page.locator('[data-pipeline="acme"]')).to_be_visible()


def test_ready_lets_the_person_change_or_turn_off_the_schedule(page, brain, notes, tmp_path):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Folder on this computer").click()
        page.get_by_label("Folder path").fill(str(notes))
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("textbox", name="Output folder").fill(str(tmp_path / "o"))
        expect(page.locator("#fr-dest-msg")).to_contain_text("New folder")
        page.get_by_role("button", name="Use this folder").click()
        page.get_by_role("button", name="Write 7 files").click()
        ready = page.locator("#fr-ready")
        expect(ready).to_contain_text("Stays current")
        ready.get_by_role("button", name="Change").click()
        page.locator("select[data-fr-schedule]").select_option("6h")
        expect(ready).to_contain_text("every 6 hours")
        ready.get_by_role("button", name="Turn off").click()
        expect(ready).to_contain_text("Automatic sync is off")
    assert load_config(brain).get("team-wiki").schedule == "off"


def test_go_to_home_shows_the_pipeline_without_a_second_background_announcement(page, brain, notes, tmp_path):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Folder on this computer").click()
        page.get_by_label("Folder path").fill(str(notes))
        page.get_by_role("button", name="Continue").click()
        page.get_by_role("button", name="Use this folder").click()
        page.get_by_role("button", name="Write 7 files").click()
        page.get_by_role("button", name="Go to Home").click()
        expect(page.locator('[data-pipeline="team-wiki"]')).to_be_visible()
        expect(page.locator("#bg-announce")).to_have_count(0)


def test_a_wrong_site_outlines_the_site_field_and_links_the_troubleshooting_page(page, brain, jira):
    jira.fail("GET", "/myself", status=404, times=9)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Jira Cloud").click()
        page.get_by_label("Jira site").fill("acme")
        page.get_by_label("Your Atlassian email").fill(EMAIL)
        page.get_by_label("API token").fill(TOKEN)
        page.get_by_role("button", name="Test and continue").click()
        expect(page.locator("#fr-error")).to_contain_text("couldn't find a Jira site at acme.atlassian.net")
        expect(page.get_by_label("Jira site")).to_have_attribute("aria-invalid", "true")
        expect(page.get_by_label("API token")).not_to_have_attribute("aria-invalid", "true")
        link = page.get_by_role("link", name="Troubleshooting: Jira token")
        expect(link).to_have_attribute("href", "/jira-token-help.md")
        shot(page, "firstrun-error-site")


def test_the_connect_step_says_which_token_to_create_and_offers_a_way_out(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Jira Cloud").click()
        expect(page.locator("main")).to_contain_text("About 2 minutes if you have a token")
        expect(page.locator("main")).to_contain_text('Choose Create API token (not "with scopes")')
        expect(page.locator("main")).to_contain_text("We never change anything in Jira")
        expect(page.locator("main")).not_to_contain_text("read-only token")      # MD-127: scoped read-only tokens are not supported yet
        expect(page.get_by_role("link", name="Create a token at Atlassian")).to_have_attribute("href", "https://id.atlassian.com/manage-profile/security/api-tokens")
        page.get_by_role("button", name="Start with a folder").click()
        expect(page.get_by_role("heading", name="Choose a folder")).to_be_visible()


def test_a_slow_review_shows_what_it_is_doing_and_how_far_not_a_static_line(page, brain, jira, monkeypatch):
    """MD-153 AC2: no screen shows only 'Looking at Jira…' while the step is working."""
    import threading

    from mdpipeline import firstrun, progress

    gate, original = threading.Event(), firstrun._review

    def slow(brain_, body):
        progress.report("reading", 120, 210)
        gate.wait(15)
        return original(brain_, body)

    monkeypatch.setattr(firstrun, "_review", slow)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Jira Cloud").click()
        page.get_by_label("Jira site").fill("acme")
        page.get_by_label("Your Atlassian email").fill(EMAIL)
        page.get_by_label("API token").fill(TOKEN)
        page.get_by_role("button", name="Test and continue").click()
        expect(page.locator("#fr-activity")).to_have_text("Reading issues from Jira… 120 of 210", timeout=5000)
        shot(page, "firstrun-review-progress")
        gate.set()
        expect(page.locator("#fr-count")).to_contain_text("6 issues")
