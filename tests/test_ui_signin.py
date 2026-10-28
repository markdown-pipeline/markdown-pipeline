"""MD-83 / MD-84 (UI backend): Sign in with GitHub from the editor; tokens held in memory until the pipeline is saved, then kept in the vault."""
import json
import re

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, github_signin as gs, vault as vault_mod
from mdpipeline.config import load_config
from secret_search import files_containing
from uihelper import running_ui

CLIENT_ID = "Iv1.testclient"
GH = {"type": "github", "repo": "acme/brain", "branch": "main", "auth": "sign-in"}


@pytest.fixture
def fake(app_data):
    f = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    f.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    f.install_app(["acme/brain"])
    github_api.set_transport(f)
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")
    yield f
    github_api.set_transport(None)


def draft(src, **kw):
    d = {"name": "p", "sources": [{"path": str(src), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"],
         "exclude": [".git/**"], "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5, "name_policy": "original"}
    d.update(kw)
    return d


def signed_in(ui, fake):
    _, start = ui.post("/api/signin/start", {})
    fake.authorize_device()
    _, done = ui.post("/api/signin/poll", {"flow": start["flow"]})
    return start, done


def test_the_state_says_whether_sign_in_is_set_up(brain, fake):
    with running_ui(brain) as ui:
        _, state = ui.get("/api/state")
        assert state["github_app"] == {"configured": True, "client_id": CLIENT_ID, "slug": "md-pipeline-test", "install_url": "https://github.com/apps/md-pipeline-test/installations/new", "source": "user", "has_default": False}
        gs.save_app_settings("", "")
        assert ui.get("/api/state")[1]["github_app"]["configured"] is False
        status, res = ui.post("/api/signin/start", {})
        assert status == 409 and "not set up" in res["error"]


def test_start_returns_the_code_the_link_and_an_opaque_flow_handle(brain, fake):
    with running_ui(brain) as ui:
        status, start = ui.post("/api/signin/start", {})
    assert status == 200 and start["user_code"] == "WDJB-MJHT" and start["verification_uri"] == "https://github.com/login/device" and start["interval"] == 5
    assert start["flow"] and "device_code" not in start and not re.search(r"\bdc\d+\b", json.dumps(start))      # the fake's device code looks like dc1; the random flow handle may contain "dc" by chance


def test_polling_is_pending_until_the_person_approves_then_returns_the_user_and_installed_repos(brain, fake):
    with running_ui(brain) as ui:
        _, start = ui.post("/api/signin/start", {})
        _, pending = ui.post("/api/signin/poll", {"flow": start["flow"]})
        assert pending["status"] == "pending" and pending["interval"] == 5
        fake.authorize_device()
        _, done = ui.post("/api/signin/poll", {"flow": start["flow"]})
    assert done["status"] == "ok" and done["login"] == "tester" and done["repos"] == ["acme/brain"] and done["pending"]
    assert "ghu_" not in json.dumps(done) and "ghr_" not in json.dumps(done)


def test_denied_and_expired_are_plain_statuses(brain, fake):
    with running_ui(brain) as ui:
        _, start = ui.post("/api/signin/start", {})
        fake.deny_device()
        _, res = ui.post("/api/signin/poll", {"flow": start["flow"]})
        assert res["status"] == "denied" and "cancelled" in res["message"].lower()
        _, start = ui.post("/api/signin/start", {})
        fake.expire_device_code()
        _, res = ui.post("/api/signin/poll", {"flow": start["flow"]})
        assert res["status"] == "error" and "start" in res["message"].lower()


def test_slow_down_is_passed_on_to_the_browser(brain, fake):
    with running_ui(brain) as ui:
        _, start = ui.post("/api/signin/start", {})
        fake.slow_down(1)
        _, res = ui.post("/api/signin/poll", {"flow": start["flow"]})
    assert res["status"] == "pending" and res["interval"] == 10


def test_test_connection_and_save_with_a_signed_in_handle_keep_the_tokens_in_the_vault_only(brain, fake, make_source, app_data):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        _, done = signed_in(ui, fake)
        _, tested = ui.post("/api/target/test", {"destination": GH, "pending": done["pending"]})
        assert tested["ok"] is True and tested["connection"]["author"] == "tester"
        status, res = ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "pending": done["pending"], "sync": True})
        assert status == 200, res
        _, state = ui.get("/api/state")
        _, view = ui.get("/api/pipelines/p")
    [secret] = vault_mod.default_vault().list()
    dest = load_config(brain).get("p").destination
    assert secret.kind == "github-sign-in" and dest["credential"] == f"vault:{secret.id}" and dest["auth"] == "sign-in"
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret.id))
    blob = json.dumps(state) + json.dumps(view) + json.dumps(res)
    assert tokens.access_token not in blob and tokens.refresh_token not in blob
    assert files_containing(tokens.access_token, brain.root, app_data.dir) == [] and files_containing(tokens.refresh_token, brain.root, app_data.dir) == []
    assert view["signin"] == {"login": "tester"} and any(p.startswith("raw/p/") for p in fake.files())


def test_an_app_not_installed_on_the_repository_is_explained_with_the_install_link(brain, fake, make_source):
    fake._installed.clear()
    with running_ui(brain) as ui:
        _, done = signed_in(ui, fake)
        assert done["repos"] == []
        _, tested = ui.post("/api/target/test", {"destination": GH, "pending": done["pending"]})
        status, res = ui.post("/api/pipelines", {"draft": draft(make_source("docs", {"a.md": "A"}), destination=GH), "pending": done["pending"]})
    assert tested["ok"] is False and "https://github.com/apps/md-pipeline-test/installations/new" in tested["errors"][0]["message"]
    assert status == 422 and load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_the_installed_repositories_can_be_checked_again_after_installing(brain, fake):
    fake._installed.clear()
    with running_ui(brain) as ui:
        _, done = signed_in(ui, fake)
        fake.install_app(["acme/brain"])
        status, res = ui.post("/api/signin/repos", {"pending": done["pending"]})
    assert status == 200 and res["repos"] == ["acme/brain"]


def test_sign_in_again_for_a_saved_pipeline_replaces_the_tokens_and_sign_out_removes_them(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        _, done = signed_in(ui, fake)
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "pending": done["pending"]})
        [secret] = vault_mod.default_vault().list()
        old = vault_mod.default_vault().get(secret.id)
        fake.revoke_tokens()
        _, again = signed_in(ui, fake)
        status, res = ui.post("/api/pipelines/p/sign-in", {"pending": again["pending"]})
        assert status == 200 and vault_mod.default_vault().get(secret.id) != old and len(vault_mod.default_vault().list()) == 1
        status, res = ui.request("DELETE", "/api/pipelines/p/sign-in")
    assert status == 200 and vault_mod.default_vault().list() == [] and not load_config(brain).get("p").destination["credential"]
    assert "github.com/settings/applications" in res["message"]


def test_a_revoked_sign_in_shows_sign_in_again_in_needs_you(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        _, done = signed_in(ui, fake)
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "pending": done["pending"]})
        fake.revoke_tokens()
        secret = vault_mod.default_vault().list()[0]
        tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret.id))
        tokens.expires_at = 1.0
        vault_mod.default_vault().replace(secret.id, tokens.to_json())
        ui.post("/api/pipelines/p/sync")
        _, act = ui.get("/api/activity")
    [card] = act["needs_you"]
    assert card["pipeline"] == "p" and card["category"] == "sign-in-expired" and "again" in card["title"].lower()
