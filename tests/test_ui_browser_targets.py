"""MD-81 / MD-82 in a real browser: the target step of the editor, the token password field, per-pipeline targets on the home cards."""
import json

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from fake_github import FakeGitHub  # noqa: E402
from mdpipeline import github_api, vault as vault_mod  # noqa: E402
from mdpipeline.config import Pipeline, load_config  # noqa: E402
from mdpipeline.engine import sync_pipeline  # noqa: E402
from secret_search import files_containing  # noqa: E402
from test_ui_browser import add_source, browser, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

TOKEN = "ghp_BROWSERSECRET0123456789"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


def new_github_pipeline(page, ui, src, name="docs"):
    open_app(page, ui)
    page.get_by_role("button", name="Advanced setup").click()
    add_source(page)
    page.locator("#f-src0").fill(str(src))
    page.locator("#f-name").fill(name)
    page.locator('[data-bind="dest-kind"][value="github"]').check()
    page.locator("#d-repo").fill("acme/brain")


def test_new_pipeline_with_a_github_target_test_connection_preview_and_save_and_sync(page, brain, fake, make_source, app_data):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    with running_ui(brain) as ui:
        new_github_pipeline(page, ui, src)
        expect(page.locator("#pv")).to_contain_text("Test the connection")
        page.locator("#d-token").fill(TOKEN)
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#dest-box .msg.ok")).to_contain_text("Connected as tester")
        expect(page.locator("#d-token")).to_be_disabled()
        assert page.locator("#d-token").input_value() == ""                       # the token is not kept in the page
        expect(page.locator("#pv")).to_contain_text("create")
        assert [r for r in fake.requests if r[0] in ("POST", "PATCH")] == []       # the preview wrote nothing
        page.get_by_role("button", name="Save & sync").click()
        expect(page.locator("li.prow", has_text="docs")).to_contain_text("GitHub")
        expect(page.locator("li.prow", has_text="docs")).to_contain_text("acme/brain")
        link = page.locator("li.prow .dest a")
        expect(link).to_be_visible()
        assert fake.head() in link.get_attribute("href")
        assert any(p.endswith(".md") and not p.startswith(".mdpipeline") for p in fake.files())        # MD-133: the repo root
        assert TOKEN not in page.content()
    [secret] = vault_mod.default_vault().list()
    assert load_config(brain).get("docs").destination["credential"] == f"vault:{secret.id}"
    assert files_containing(TOKEN, brain.root, app_data.dir) == []


def test_a_wrong_token_shows_a_plain_error_and_saves_nothing(page, brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        new_github_pipeline(page, ui, src)
        page.locator("#d-token").fill("ghp_WRONGWRONG0000")
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#dest-box .msg.err")).to_contain_text("rejected")
        assert "ghp_WRONG" not in page.content()
        page.get_by_role("button", name="Save", exact=True).click()           # the form still offers Save: the server refuses the bad token
        expect(page.locator("#acts .why").first).to_contain_text("rejected")
    assert load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_the_home_cards_show_each_pipelines_own_target(page, brain, fake, make_source, add_pipeline, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    add_pipeline(Pipeline.new("alpha", [str(make_source("a", {"a.md": "A"}))]))
    add_pipeline(Pipeline.new("beta", [str(make_source("b", {"b.md": "B"}))], destination={"type": "directory", "path": str(vault)}))
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("li.prow", has_text="alpha")).to_contain_text(str(brain.root))
        expect(page.locator("li.prow", has_text="beta")).to_contain_text(str(vault))
        expect(page.locator(".tcard")).to_have_count(0)                              # the brain-level target card is gone


def test_a_directory_target_can_be_chosen_in_the_editor(page, brain, make_source, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        add_source(page)
        page.locator("#f-src0").fill(str(src))
        page.locator("#d-dir").fill(str(vault))
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#dest-box .msg.ok")).to_contain_text("writable")
        page.get_by_role("button", name="Save & sync").click()
        expect(page.locator("li.prow", has_text="docs")).to_contain_text(str(vault))
    assert list(vault.rglob("*a.md"))


def test_replace_a_token_in_the_target_settings_keeps_the_old_one_when_the_new_is_refused(page, brain, fake, make_source):
    from mdpipeline.targetsetup import store_token
    from mdpipeline.targetconfig import GitHubSpec

    spec, secret_id = store_token(GitHubSpec("acme/brain", "x", "main"), TOKEN)
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))], destination=spec.to_dict()))
    from mdpipeline.config import save_config

    save_config(brain, cfg)
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow").get_by_role("button", name="Edit").click()
        expect(page.locator("#d-tokstate")).to_contain_text("a token is set")
        page.locator("#d-newtoken").fill("ghp_NOTTHEONE0000000")
        page.get_by_role("button", name="Replace token").click()
        expect(page.locator("#dest-box")).to_contain_text("old token is unchanged")
        assert "ghp_NOTTHEONE" not in page.content()
    assert vault_mod.default_vault().get(secret_id) == TOKEN


def test_needs_you_shows_a_locked_vault_with_the_pipeline_name(page, brain, fake, make_source, app_data):
    from mdpipeline.targetsetup import store_token
    from mdpipeline.targetconfig import GitHubSpec
    from mdpipeline.config import save_config

    spec, _ = store_token(GitHubSpec("acme/brain", "x", "main"), TOKEN)
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline.new("p", [str(make_source("d", {"a.md": "A"}))], destination=spec.to_dict()))
    save_config(brain, cfg)
    app_data.keychain.lose()
    assert sync_pipeline(brain, "p", trigger="ui").category == "vault-locked"
    with running_ui(brain) as ui:
        open_app(page, ui, "activity")
        card = page.locator(".ecard")
        expect(card).to_contain_text("p: The vault is locked")
        expect(card).to_contain_text("nothing was written")
        assert TOKEN not in page.content()


# --- Sign in with GitHub (MD-83) --------------------------------------------------------------------------------------------
def app_fake(app_data):
    from mdpipeline import github_signin as gs

    f = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    f.enable_app(client_id="Iv1.testclient", slug="md-pipeline-test")
    f.install_app(["acme/brain"])
    f.device_interval = 1
    github_api.set_transport(f)
    gs.save_app_settings("Iv1.testclient", "md-pipeline-test")
    return f


def test_sign_in_with_github_is_the_default_shows_the_code_then_the_repos_and_saves_with_the_tokens_in_the_vault(page, brain, make_source, app_data):
    fake = app_fake(app_data)
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    try:
        with running_ui(brain) as ui:
            open_app(page, ui)
            page.get_by_role("button", name="Advanced setup").click()
            add_source(page)
            page.locator("#f-src0").fill(str(src))
            page.locator("#f-name").fill("docs")
            page.locator('[data-bind="dest-kind"][value="github"]').check()
            expect(page.locator('[data-bind="dest-auth"][value="sign-in"]')).to_be_checked()
            page.get_by_role("button", name="Sign in with GitHub").click()
            expect(page.locator("#d-code")).to_have_text("WDJB-MJHT")
            expect(page.locator("#d-signin a")).to_have_attribute("href", "https://github.com/login/device")
            fake.authorize_device()                                           # the person approves on github.com
            expect(page.locator("#d-signin .msg.ok")).to_contain_text("Signed in as tester", timeout=30000)
            page.locator("#d-repopick").select_option("acme/brain")
            expect(page.locator("#pv")).to_contain_text("create")
            page.get_by_role("button", name="Test connection").click()
            expect(page.locator("#dest-box .msg.ok").last).to_contain_text("Connected as tester")
            page.get_by_role("button", name="Save & sync").click()
            expect(page.locator("li.prow", has_text="docs")).to_contain_text("acme/brain")
            assert any(p.endswith(".md") and not p.startswith(".mdpipeline") for p in fake.files())
            html = page.content()
        [secret] = vault_mod.default_vault().list()
        from mdpipeline import github_signin as gs

        tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret.id))
        assert secret.kind == "github-sign-in" and tokens.access_token not in html and tokens.refresh_token not in html
        assert files_containing(tokens.access_token, brain.root, app_data.dir) == []
    finally:
        github_api.set_transport(None)


def test_an_app_not_installed_on_the_chosen_repository_offers_the_install_link_and_check_again(page, brain, make_source, app_data):
    fake = app_fake(app_data)
    fake._installed.clear()
    try:
        with running_ui(brain) as ui:
            open_app(page, ui)
            page.get_by_role("button", name="Advanced setup").click()
            add_source(page)
            page.locator("#f-src0").fill(str(make_source("docs", {"a.md": "A"})))
            page.locator('[data-bind="dest-kind"][value="github"]').check()
            page.locator("#d-repo").fill("acme/brain")
            page.get_by_role("button", name="Sign in with GitHub").click()
            expect(page.locator("#d-code")).to_be_visible()                       # the device code exists once the code is shown
            fake.authorize_device()
            expect(page.locator("#d-signin .msg.ok")).to_contain_text("Signed in as tester", timeout=30000)
            expect(page.locator("#d-signin")).to_contain_text("not installed on acme/brain")
            expect(page.locator('#d-signin a[href="https://github.com/apps/md-pipeline-test/installations/new"]')).to_be_visible()
            fake.install_app(["acme/brain"])
            page.get_by_role("button", name="check again").click()
            expect(page.locator("#d-repopick")).to_be_visible()
    finally:
        github_api.set_transport(None)


def test_without_a_registered_app_the_sign_in_option_explains_and_the_token_option_still_works(page, brain, make_source, app_data):
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        page.locator('[data-bind="dest-kind"][value="github"]').check()
        expect(page.locator('[data-bind="dest-auth"][value="token"]')).to_be_checked()
        page.locator('[data-bind="dest-auth"][value="sign-in"]').check()
        expect(page.locator("#dest-box")).to_contain_text("not set up on this computer yet")
        expect(page.locator("#dest-box")).to_contain_text("Personal access token")
        expect(page.locator('#dest-box a[href="/github-app-registration.md"]')).to_be_visible()


# --- change target (MD-85) ----------------------------------------------------------------------------------------------------
def test_change_target_shows_the_plan_and_what_stays_behind_then_moves(page, brain, brain_dir, fake, make_source, add_pipeline, tmp_path):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)], name_policy="date-name"))
    sync_pipeline(brain, "p", trigger="cli")
    fake.commit_as_other({p.relative_to(brain_dir).as_posix(): p.read_bytes() for p in (brain_dir / "raw").rglob("*.md")}, "owner pushes the brain")
    before = {p: p.read_bytes() for p in (brain_dir / "raw").rglob("*.md")}
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.locator("li.prow").get_by_role("button", name="Edit").click()
        expect(page.locator('[data-bind="dest-kind"][value="folder"]')).to_be_disabled()      # an edit does not move the pipeline
        page.get_by_role("button", name="Change target…").click()
        page.locator('[data-bind="dest-kind"][value="github"]').check()
        page.locator('[data-bind="dest-auth"][value="token"]').check()
        page.locator("#d-repo").fill("acme/brain")
        page.locator("#d-token").fill(TOKEN)
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#dest-box .msg.ok")).to_contain_text("Connected as tester")
        expect(page.locator("#change-note")).to_contain_text("2 owned files, 2 already there with the same bytes, 0 missing")
        expect(page.locator("#change-note")).to_contain_text("stay untouched")
        page.get_by_role("button", name="Move and save").click()
        expect(page.locator("li.prow", has_text="p")).to_contain_text("acme/brain")
    assert {p: p.read_bytes() for p in (brain_dir / "raw").rglob("*.md")} == before            # the old target is byte-identical
    assert load_config(brain).get("p").destination["repo"] == "acme/brain"


def test_the_client_id_can_be_entered_in_the_ui_and_sign_in_appears_without_a_restart(page, brain, app_data):
    from mdpipeline import github_signin as gs

    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        page.locator('[data-bind="dest-kind"][value="github"]').check()
        page.locator('[data-bind="dest-auth"][value="sign-in"]').check()
        expect(page.get_by_role("button", name="Sign in with GitHub")).to_have_count(0)
        page.locator("#a-client").fill("nope")
        page.locator("#a-slug").fill("My App!")
        page.locator("#d-appform").get_by_role("button", name="Save").click()
        expect(page.locator("#d-appform .msg.err").first).to_be_visible()
        assert gs.app_settings()["client_id"] == ""
        page.locator("#a-client").fill("Iv23liAbCdEf123456")
        page.locator("#a-slug").fill("my-app")
        page.locator("#d-appform").get_by_role("button", name="Save").click()
        expect(page.get_by_role("button", name="Sign in with GitHub")).to_be_visible()
        expect(page.locator("#d-appline")).to_contain_text("Iv23liAbCdEf123456")
        page.get_by_role("button", name="Change").click()
        page.locator("#a-slug").fill("other-app")
        page.locator("#d-appform").get_by_role("button", name="Save").click()
        expect(page.locator("#d-appline")).to_contain_text("other-app")
    assert gs.app_settings() == {"client_id": "Iv23liAbCdEf123456", "slug": "other-app"}
    assert not (app_data.dir / "vault.json").exists()


def test_with_a_shipped_default_sign_in_is_ready_without_setup_and_the_default_can_be_restored(page, brain, app_data, monkeypatch):
    from mdpipeline import github_signin as gs

    monkeypatch.setattr(gs, "shipped_github_app", lambda: {"client_id": "Iv23liShippedDefault1", "slug": "shipped-app"})
    with running_ui(brain) as ui:
        open_app(page, ui)
        page.get_by_role("button", name="Advanced setup").click()
        page.locator('[data-bind="dest-kind"][value="github"]').check()
        page.locator('[data-bind="dest-auth"][value="sign-in"]').check()
        expect(page.get_by_role("button", name="Sign in with GitHub")).to_be_visible()          # no setup step is shown
        expect(page.locator("#d-appform")).to_have_count(0)
        expect(page.locator("#d-appline")).to_contain_text("shipped default")
        page.get_by_role("button", name="Change").click()
        page.locator("#a-client").fill("Iv23liAbCdEf123456")
        page.locator("#a-slug").fill("my-app")
        page.locator("#d-appform").get_by_role("button", name="Save").click()
        expect(page.locator("#d-appline")).to_contain_text("your own app")
        page.get_by_role("button", name="Use the shipped default").click()
        expect(page.locator("#d-appline")).to_contain_text("shipped default")
        expect(page.locator("#d-appline")).to_contain_text("shipped-app")
    assert gs.app_source() == "default"


# MD-143: the branch is chosen from the repository's branches; typing stays available ------------------------------------

def test_the_branch_can_be_chosen_from_a_list_with_the_default_preselected_and_typing_still_works(page, brain, fake, make_source, app_data):
    fake.add_branch("md-sync")
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        new_github_pipeline(page, ui, src)
        page.locator("#d-token").fill(TOKEN)
        page.get_by_role("button", name="Choose a branch").click()
        picker = page.locator("#d-branchlist")
        expect(picker).to_contain_text("main")
        expect(picker).to_contain_text("default")
        expect(picker.get_by_role("radio", name="main", exact=False)).to_be_checked()          # default branch preselected
        picker.get_by_label("md-sync").check()
        expect(page.locator("#d-branch")).to_have_value("md-sync")
        picker.get_by_label("main").check()
        expect(page.locator("#d-branch")).to_have_value("")                                    # empty = the default branch, as before
        page.locator("#d-branch").fill("my-own-branch")                                        # typing is still there
        expect(page.locator("#d-branch")).to_have_value("my-own-branch")
        assert TOKEN not in page.content()


def test_choosing_a_branch_without_a_token_says_what_is_missing(page, brain, fake, make_source, app_data):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        new_github_pipeline(page, ui, src)
        page.get_by_role("button", name="Choose a branch").click()
        expect(page.locator("#d-branchlist")).to_contain_text("token")
        assert [r for r in fake.requests if r[1].endswith("/branches")] == []


def test_the_branch_list_works_after_test_connection_with_the_held_token_and_the_save_uses_the_chosen_branch(page, brain, fake, make_source, app_data):
    fake.add_branch("md-sync")
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        new_github_pipeline(page, ui, src)
        page.locator("#d-token").fill(TOKEN)
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#dest-box .msg.ok")).to_contain_text("Connected as tester")
        page.get_by_role("button", name="Choose a branch").click()
        page.locator("#d-branchlist").get_by_label("md-sync").check()
        expect(page.locator("#d-branch")).to_have_value("md-sync")
        page.get_by_role("button", name="Save & sync").click()
        expect(page.locator("li.prow", has_text="docs")).to_contain_text("GitHub")
    assert load_config(brain).get("docs").destination["branch"] == "md-sync"
    assert any(p.endswith(".md") for p in fake.files(fake.head("md-sync")))
