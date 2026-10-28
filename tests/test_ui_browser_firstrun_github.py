"""MD-152 in a real browser: on Review, 'Your team's GitHub repo' next to the folder (folder stays the default), with the editor's sign-in and repo list."""
import re

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_github import FakeGitHub  # noqa: E402
from mdpipeline import autostart, github_api, github_signin as gs, notices  # noqa: E402
from mdpipeline.config import load_config  # noqa: E402
from test_ui_browser import browser, open_app, page, shot  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

CLIENT_ID = "Iv1.testclient"
SLOW = 20000          # the fake's sign-in poll interval is 5 s


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def notes(make_source):
    return make_source("team-wiki", {f"n{i}.md": f"# Note {i}\n" for i in range(4)})


def make_fake(installed=("acme/team-docs",)):
    f = FakeGitHub(repo="acme/team-docs", branch="main", token="pat", files={"README.md": b"hello"})
    f.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    f.install_app(list(installed))
    github_api.set_transport(f)
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")
    return f


@pytest.fixture
def fake(app_data):
    f = make_fake()
    yield f
    github_api.set_transport(None)


def to_target(page, ui, notes):
    open_app(page, ui)
    page.get_by_role("button", name="Folder on this computer").click()
    page.get_by_label("Folder path").fill(str(notes))
    expect(page.locator("#fr-folder-msg")).to_contain_text("Folder found")
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", name="Where should your files go?")).to_be_visible()


def sign_in_and_use_repo(page, fake):
    page.locator("[data-card=github]").get_by_role("button", name="Sign in with GitHub").click()
    fake.authorize_device()
    use = page.get_by_role("button", name="Use this repo")
    expect(use).to_be_enabled(timeout=SLOW)
    use.click()


# MD-159 moved the GitHub choice from a radio on Review to its own card on "Where should your files go?" (the full path is in
# test_ui_browser_firstrun_flow.py); what stays here are the GitHub-specific edge cases.
def test_the_app_installed_on_no_repository_gives_the_install_link_and_no_use_this_repo_button(page, brain, notes, app_data):
    f = make_fake(installed=())
    try:
        with running_ui(brain) as ui:
            to_target(page, ui, notes)
            page.locator("[data-card=github]").get_by_role("button", name="Sign in with GitHub").click()
            f.authorize_device()
            expect(page.locator("#fr-gh")).to_contain_text("isn't installed on any of your repos", timeout=SLOW)
            expect(page.get_by_role("link", name="Install it on a repo")).to_have_attribute("href", re.compile(r"/apps/md-pipeline-test/installations/new"))
            expect(page.get_by_role("button", name="Check again")).to_be_visible()
            expect(page.get_by_role("button", name="Use this repo")).to_have_count(0)
            expect(page.get_by_role("button", name=re.compile(r"^(Commit|Write)"))).to_have_count(0)
    finally:
        github_api.set_transport(None)


def test_choosing_the_folder_after_the_repo_discards_the_github_choice(page, brain, notes, fake, tmp_path):
    with running_ui(brain) as ui:
        to_target(page, ui, notes)
        sign_in_and_use_repo(page, fake)
        expect(page.get_by_role("button", name="Commit 4 files to acme/team-docs")).to_be_enabled()
        page.get_by_role("button", name="Back").click()
        expect(page.locator("[data-card=github]")).to_contain_text("Your choice")
        page.get_by_role("button", name="Use this folder").click()
        expect(page.get_by_role("button", name=re.compile(r"^Write 4 files to"))).to_be_enabled()
        expect(page.locator("#fr-gh")).to_have_count(0)
        head = fake.head()
        page.get_by_role("button", name=re.compile(r"^Write 4 files to")).click()
        expect(page.locator("#fr-ready")).to_contain_text("4 files in")
        assert fake.head() == head
    assert load_config(brain).get("team-wiki").destination["type"] == "directory"


def test_the_target_step_event_says_github_or_directory_and_never_the_repository(page, brain, notes, fake):
    """MD-152 AC6 / MD-159 AC11: content-free properties `target` and `dest_changed`; no repo, owner, login or path anywhere in the queued events."""
    import json

    from mdpipeline import usage
    from mdpipeline.usage import UsageStore

    UsageStore().decide("share")
    with running_ui(brain) as ui:
        to_target(page, ui, notes)
        sign_in_and_use_repo(page, fake)
        page.get_by_role("button", name="Commit 4 files to acme/team-docs").click(timeout=SLOW)
        expect(page.locator("#fr-ready")).to_contain_text("4 files in acme/team-docs", timeout=SLOW)
        page.wait_for_timeout(500)
    queue = json.loads((usage.data_dir() / "usage.json").read_text(encoding="utf-8")).get("queue", [])
    done = [e["properties"] for e in queue if e["event"] == "onboarding_step_completed" and e["properties"].get("step") == "choose_target"]
    assert done and done[-1]["target"] == "github" and done[-1]["dest_changed"] is False
    blob = json.dumps(queue)
    assert "acme" not in blob and "team-docs" not in blob and "tester" not in blob
