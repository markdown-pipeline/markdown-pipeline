"""MD-116 in a real browser: what the page reports (new source, view mode, Activity, snippet copy, dry run, errors), and that it reports
nothing, not even a request, without consent."""
import json
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_jira import FakeJira  # noqa: E402
from mdpipeline import jira_api, usage  # noqa: E402
from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from mdpipeline.usage import UsageStore  # noqa: E402
from test_jira_sync import EMAIL, SITE, TOKEN  # noqa: E402
from test_ui_browser import add_source, browser, open_activity, open_app, page, show_manual_agent_command  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

CANARY_PATH = "private-client-folder"


def own(e):
    return {k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")}


def queued(*names):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def wait_for(name, count=1, timeout=5.0):
    """The page reports in the background: wait until the server has queued ``count`` of ``name``."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if len(queued(name)) >= count:
            return queued(name)
        time.sleep(0.05)
    raise AssertionError(f"{name} x{count} was not reported; queue: {[e['event'] for e in queued()]}")


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_issue("MD", "issue 0")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def test_choosing_a_folder_source_on_an_empty_home_reports_source_add_started_from_the_empty_state(page, brain, sharing):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        time.sleep(0.4)
        assert queued("source_add_started") == []                       # opening the editor alone says nothing: no source type is chosen yet
        add_source(page, "Folder")
        (e,) = wait_for("source_add_started")
    assert own(e) == {"source_type": "folder", "entry_point": "empty_state"}


def test_choosing_a_jira_source_reports_jira_and_later_additions_report_the_editor(page, brain, sharing, add_pipeline, make_source):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        add_source(page, "Jira")
        first = wait_for("source_add_started")[-1]
    assert own(first) == {"source_type": "jira", "entry_point": "empty_state"}
    add_pipeline(Pipeline.new("existing", [str(make_source("a", {"1.md": "1"}))]))
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="+ New pipeline").first.click()
        add_source(page, "Folder")
        events = wait_for("source_add_started", 2)
    assert own(events[-1]) == {"source_type": "folder", "entry_point": "editor"}


def test_the_activity_tab_reports_activity_viewed_with_where_it_came_from(page, brain, sharing):
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_activity(page)
        (e,) = wait_for("activity_viewed")
    assert own(e) == {"entry_point": "onboarding"}              # an empty brain opens on the guided first run


def test_copying_the_agent_snippet_reports_which_agent(page, brain, sharing, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    with running_ui(brain) as ui:
        open_app(page, ui)
        show_manual_agent_command(page)
        page.get_by_role("button", name="Copy").first.click()
        page.get_by_role("button", name="Codex", exact=True).click()
        page.get_by_role("button", name="Copy").first.click()
        events = wait_for("agent_snippet_copied", 2)
    assert [own(e) for e in events] == [{"agent": "claude_code"}, {"agent": "codex"}]


def test_opening_view_mode_reports_it_from_home(page, brain, sharing, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow a", has_text="p").first.click()
        (e,) = wait_for("view_mode_opened")
    assert own(e) == {"entry_point": "home"}


def test_the_dry_run_is_reported_once_per_editor_session_as_buckets_never_names(page, brain, sharing, make_source):
    src = make_source(CANARY_PATH, {f"{i}.md": str(i) for i in range(3)})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        add_source(page, "Folder")
        page.get_by_label("Folder 1 path").fill(str(src))
        expect(page.locator("#pv")).to_contain_text("3")
        (e,) = wait_for("dry_run_viewed")
        page.locator("#f-template").fill("{pipeline}/{relpath}")          # so the pipeline name shows in the preview paths (MD-133: the default has none)
        page.locator("#f-name").fill("renamed-pipeline")                    # a second preview of the same editor session
        expect(page.locator("#pv")).to_contain_text("renamed-pipeline")
        time.sleep(0.6)
    assert own(e) == {"items_bucket": "1-10", "would_delete_bucket": "0"}
    assert len(queued("dry_run_viewed")) == 1
    assert CANARY_PATH not in json.dumps(queued()) and "renamed-pipeline" not in json.dumps(queued())


def test_a_failed_credential_check_reports_error_shown_with_a_class_and_no_text(page, brain, sharing, fake):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").first.click()
        add_source(page, "Jira")
        for key, value in {"site": SITE, "jql": "project = = =", "email": EMAIL, "token": TOKEN}.items():
            page.locator(f'[data-bind="jira-{key}"]').fill(value)
        page.locator(".jirarow").get_by_role("button", name="Test connection").click()
        expect(page.locator(".jirarow .msg.err")).to_be_visible()
        (e,) = wait_for("error_shown")
    assert own(e) == {"ui_area": "source", "error_class": "validation"}
    assert "project = = =" not in json.dumps(queued()) and SITE not in json.dumps(queued()) and "Error in the JQL" not in json.dumps(queued())


def test_without_consent_the_page_does_not_even_send_a_usage_request(page, brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/api/usage/event" in r.url else None)
    with running_ui(brain) as ui:
        open_app(page, ui)
        open_activity(page)
        page.get_by_role("button", name="Pipelines").click()
        show_manual_agent_command(page)
        page.get_by_role("button", name="Copy").first.click()
        page.get_by_role("button", name="+ New pipeline").first.click()
        time.sleep(0.5)
    assert requests == [] and UsageStore().pending == 0
