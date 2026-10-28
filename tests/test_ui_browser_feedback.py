"""MD-123 in a real browser: request a source from the Start screen, and the one-question prompts."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, notices, usage  # noqa: E402
from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from test_usage_emit import Recorder  # noqa: E402
from uihelper import running_ui  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def transport(monkeypatch):
    t = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: t)
    return t


def synced(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    ActivityLog(brain).append({"type": "run", "pipeline": "acme", "trigger": "ui", "result": "ok", "counts": {"created": 1}, "errors": []})


# request a source -------------------------------------------------------------------------------------------------
def test_start_offers_the_tools_we_do_not_have_yet_as_chips(page, brain):
    with running_ui(brain) as ui:
        open_app(page, ui)
        group = page.get_by_role("group", name="Use another tool? Tell us which")
        for name in ("Confluence", "Notion", "Google Drive", "GitHub Issues", "Linear", "Slack", "SharePoint", "Something else"):
            expect(group.get_by_role("button", name=name)).to_be_visible()
        shot(page, "request-chips")


def test_a_chip_opens_the_panel_and_sends_one_request_even_with_sharing_off(page, brain, transport):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Notion", exact=True).click()
        panel = page.locator("#fb-request")
        expect(panel).to_contain_text("Request Notion")
        expect(panel).to_contain_text("even if you don't share usage")
        expect(panel).to_contain_text("goes to the team")
        shot(page, "request-panel")
        panel.get_by_role("button", name="Send request").click()
        expect(panel).to_contain_text("✓ Request for Notion sent")
        expect(panel).to_contain_text("We can't promise which tools or when")
        expect(panel).not_to_contain_text("soon")
        expect(page.get_by_role("button", name="Jira Cloud")).to_be_visible()          # the person stays on the screen
    (event,) = transport.events
    assert event["event"] == "source_requested" and event["chosen"] is True and event["properties"]["source_id"] == "notion"
    assert "email" not in event["properties"]


def test_something_else_asks_which_tool_and_sends_the_words_and_the_email_only_on_send(page, brain, transport):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Something else").click()
        page.get_by_label("Which tool?").fill("Linear")
        page.get_by_label("Email me if we add it (optional)").fill("dana@acme.dev")
        assert transport.events == []                                   # typing sends nothing
        page.get_by_role("button", name="Send request").click()
        expect(page.locator("#fb-request")).to_contain_text("✓ Request sent")
    props = transport.events[0]["properties"]
    assert (props["source_id"], props["other_source"], props["email"]) == ("other", "Linear", "dana@acme.dev")


def test_a_bad_email_is_explained_and_nothing_is_sent(page, brain, transport):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Slack", exact=True).click()
        page.get_by_label("Email me if we add it (optional)").fill("nope")
        page.get_by_role("button", name="Send request").click()
        expect(page.locator("#fb-error[role=alert]")).to_contain_text("doesn't look like an email")
    assert transport.events == []


def test_a_failed_send_says_so_and_keeps_the_panel_open(page, brain, monkeypatch):
    def offline(batch):
        raise OSError("down")

    monkeypatch.setattr(usage, "get_transport", lambda: offline)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Notion", exact=True).click()
        page.get_by_role("button", name="Send request").click()
        expect(page.locator("#fb-error")).to_contain_text("couldn't send this right now")
        expect(page.get_by_role("button", name="Send request")).to_be_enabled()


def test_a_jira_data_center_site_offers_to_request_it(page, brain, transport):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Jira Cloud").click()
        page.get_by_label("Jira site").fill("jira.corp.example.com")
        page.get_by_label("Your Atlassian email").fill("dana@corp.example.com")
        page.get_by_label("API token").fill("x")
        page.get_by_role("button", name="Test and continue").click()
        expect(page.locator("#fr-error")).to_contain_text("Jira Data Center or Server")
        page.get_by_role("button", name="Request Jira Data Center").click()
        expect(page.locator("#fr-error")).to_contain_text("✓ Request sent")
    assert transport.events[0]["properties"]["source_id"] == "jira_data_center"


# one question, three moments ---------------------------------------------------------------------------------------
def test_after_the_first_sync_home_asks_one_question_and_an_answer_goes_to_the_team_on_send(page, brain, add_pipeline, make_source, transport):
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        card = page.locator("#fb-prompt")
        expect(card).to_contain_text("ONE QUESTION")
        expect(card).to_contain_text("Did you get what you expected?")
        expect(card).to_contain_text("goes to the team")
        shot(page, "prompt-home")
        card.get_by_role("button", name="Partly").click()
        card.get_by_label("Anything to add? (optional)").fill("Missing the comments")
        assert transport.events == []                                    # choosing and typing send nothing
        card.get_by_role("button", name="Send").click()
        expect(page.locator("#fb-thanks")).to_contain_text("Thanks")
        expect(page.locator("#fb-prompt")).to_have_count(0)
    props = transport.events[0]["properties"]
    assert (props["question_id"], props["answer"], props["comment"]) == ("first_sync_expectation", "partly", "Missing the comments")


def test_a_prompt_is_shown_once_even_if_ignored(page, brain, add_pipeline, make_source, transport):
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#fb-prompt")).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name="Pipelines")).to_be_visible()
        expect(page.locator("#fb-prompt")).to_have_count(0)
    assert transport.events == []


def test_dismiss_removes_the_card_and_sends_nothing_and_it_never_blocks(page, brain, add_pipeline, make_source, transport):
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.get_by_role("button", name="Sync now")).to_be_enabled()          # usable while the card is up
        page.locator("#fb-prompt").get_by_role("button", name="Dismiss").click()
        expect(page.locator("#fb-prompt")).to_have_count(0)
    assert transport.events == []


def test_removing_the_last_source_asks_why_on_the_start_screen_and_start_still_works(page, brain, transport):
    ActivityLog(brain).append({"type": "run", "pipeline": "acme", "trigger": "ui", "result": "ok", "counts": {"created": 2}, "errors": []})
    ActivityLog(brain).event("remove", "acme", "ui", unowned=2, deleted=0, purge=False, paths=[])
    with running_ui(brain) as ui:
        open_app(page, ui)
        card = page.locator("#fb-prompt")
        expect(card).to_contain_text("Why did you remove it?")
        card.get_by_role("button", name="I was only testing").click()
        card.get_by_role("button", name="Send").click()
        expect(page.locator("#fb-thanks")).to_be_visible()
        page.get_by_role("button", name="Folder on this computer").click()
        expect(page.get_by_role("heading", name="Choose a folder")).to_be_visible()
    assert transport.events[0]["properties"]["answer"] == "just_testing"
