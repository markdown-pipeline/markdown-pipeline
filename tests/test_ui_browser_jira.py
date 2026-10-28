"""MD-53 in a real browser: add a Jira source in the editor, Test connection, live preview, save, and Needs you for a rejected token."""
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import jira_api, vault as vault_mod  # noqa: E402
from mdpipeline.config import Pipeline, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source  # noqa: E402
from test_ui_browser import add_source, browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def fill(page, **values):
    for key, value in values.items():
        page.locator(f'[data-bind="jira-{key}"]').fill(value)


def test_add_a_jira_source_test_connection_preview_and_save_and_sync(page, brain, fake):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        add_source(page, "Jira")                                                           # a Jira-only pipeline: no folder row at all
        fill(page, site=SITE, jql="project = MD", email=EMAIL, token=TOKEN)
        page.locator(".jirarow").get_by_role("button", name="Test connection").click()
        expect(page.locator(".jirarow .msg.ok")).to_contain_text("Connected as Mia Owner")
        expect(page.locator(".jirarow .msg.ok")).to_contain_text("3 issues")
        expect(page.locator('[data-bind="jira-token"]')).to_be_disabled()                  # the token is checked and not kept in the page
        page.locator("#f-name").fill("jirap")
        expect(page.locator("#pv")).to_contain_text("MD-1 issue 0")
        expect(page.locator("#pv")).to_contain_text("create")
        page.get_by_role("button", name="Save & sync").click()
        expect(page.locator("#toast")).to_contain_text("synced")
        assert TOKEN not in page.content() and EMAIL not in page.content()
    src = load_config(brain).get("jirap").sources[0]
    assert src.type == "jira" and src.options["token_ref"].startswith("vault:") and src.options["email_ref"].startswith("vault:")
    assert sorted(i.kind for i in vault_mod.default_vault().list()) == ["jira-email", "jira-token"] and TOKEN not in brain.config_path.read_text()
    assert [p for p in brain.root.rglob("*.md") if ".mdpipeline" not in p.parts]        # MD-133: at the root of the output folder


def test_test_connection_failure_is_shown_in_place_with_jiras_message(page, brain, fake):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        add_source(page, "Jira")
        fill(page, site=SITE, jql="project = = =", email=EMAIL, token=TOKEN)
        page.locator(".jirarow").get_by_role("button", name="Test connection").click()
        expect(page.locator(".jirarow .msg.err")).to_contain_text("Error in the JQL Query")


def test_needs_you_shows_the_source_category_and_the_next_step_for_a_rejected_token(page, brain, fake, add_pipeline, monkeypatch):
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    assert sync_pipeline(brain, "p", trigger="ui").result == "error"
    with running_ui(brain) as ui:
        open_app(page, ui, "activity")
        card = page.locator(".ecard:not([data-card=\"import\"])")
        expect(card).to_contain_text("Jira rejected the token")
        expect(card).to_contain_text("create a new API token")
        expect(card).to_contain_text("JIRA_TOKEN")
        expect(card).to_contain_text("Nothing it owns was deleted")
        assert "ATATT-WRONG" not in page.content()


def test_the_editor_loads_a_saved_jira_source_and_never_shows_a_token(page, brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name"))
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow").get_by_role("button", name="Edit").click()
        expect(page.locator('[data-bind="jira-site"]')).to_have_value(SITE)
        expect(page.locator(".jirarow")).to_contain_text("older setup")                   # a source made before the vault: its references are shown, not a token
        expect(page.locator(".jirarow")).to_contain_text("env:JIRA_TOKEN")
        assert TOKEN not in page.content()


def test_a_saved_vault_source_shows_that_its_credentials_are_kept_and_never_shows_them(page, brain, fake):
    from mdpipeline.jirasetup import apply_typed
    from mdpipeline.config import Source, save_config

    pipeline = Pipeline("p", [Source(SITE, "jira", type="jira", options={"site": SITE, "jql": "project = MD", "_email": EMAIL, "_token": TOKEN})], name_policy="date-name")
    apply_typed(pipeline)
    cfg = load_config(brain)
    cfg.pipelines.append(pipeline)
    save_config(brain, cfg)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow").get_by_role("button", name="Edit").click()
        expect(page.locator(".jirarow")).to_contain_text("kept in the app vault")
        assert TOKEN not in page.content() and EMAIL not in page.content()
