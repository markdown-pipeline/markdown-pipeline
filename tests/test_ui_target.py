"""MD-81 / MD-82 (backend): the UI chooses a pipeline's target; a GitHub token is sent once, checked, kept in the vault, never sent back."""
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, vault as vault_mod
from mdpipeline.config import Pipeline, load_config
from secret_search import files_containing
from uihelper import running_ui

TOKEN = "ghp_UIUIUISECRET0123456789"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


def draft(src, **kw):
    d = {"name": "p", "sources": [{"path": str(src), "label": "docs"}], "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"],
         "exclude": [".git/**"], "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5, "name_policy": "original"}
    d.update(kw)
    return d


GH = {"type": "github", "repo": "acme/brain", "branch": "main", "auth": "token"}


def test_test_connection_with_a_token_reports_the_connection_and_returns_only_an_opaque_handle(brain, fake):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
    assert status == 200 and res["ok"] is True and res["connection"]["user"] == "tester" and res["connection"]["write_allowed"] is True
    assert res["pending"] and TOKEN not in json.dumps(res) and vault_mod.default_vault().list() == []


def test_test_connection_failures_are_plain_and_store_nothing(brain, fake):
    fake.set_permission("read")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
        assert res["ok"] is False and "read and write" in res["errors"][0]["message"] and "pending" not in res
        _, res = ui.post("/api/target/test", {"destination": GH, "token": "ghp_WRONGWRONG0000"})
        assert res["ok"] is False and "rejected" in res["errors"][0]["message"] and "ghp_WRONG" not in json.dumps(res)
    assert vault_mod.default_vault().list() == []


def test_test_connection_of_a_directory_checks_the_folder(brain, tmp_path):
    with running_ui(brain) as ui:
        _, ok = ui.post("/api/target/test", {"destination": {"type": "directory", "path": str(tmp_path)}})
        _, bad = ui.post("/api/target/test", {"destination": {"type": "directory", "path": str(tmp_path / "nope")}})
    assert ok["ok"] is True and bad["ok"] is False and "does not exist" in bad["errors"][0]["message"]


def test_creating_a_pipeline_with_a_typed_token_stores_it_in_the_vault_and_never_echoes_it(brain, fake, make_source, app_data):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN})
        assert status == 200 and res["saved"] is True and TOKEN not in json.dumps(res)
        _, state = ui.get("/api/state")
        _, view = ui.get("/api/pipelines/p")
    [secret] = vault_mod.default_vault().list()
    dest = load_config(brain).get("p").destination
    assert dest["credential"] == f"vault:{secret.id}" and dest["repo"] == "acme/brain"
    assert TOKEN not in json.dumps(state) + json.dumps(view) and files_containing(TOKEN, brain.root, app_data.dir) == []
    assert view["draft"]["destination"]["credential"] == f"vault:{secret.id}" and view["has_credential"] is True


def test_a_tested_token_can_be_saved_by_its_handle_without_typing_it_again(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        _, tested = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
        status, res = ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "pending": tested["pending"]})
        assert status == 200, res
        again, _ = ui.post("/api/pipelines", {"draft": draft(src, name="q", destination=GH), "pending": tested["pending"]})
    assert again == 422          # the handle is single use: a second save needs a token
    assert vault_mod.default_vault().get(load_config(brain).get("p").destination["credential"][len("vault:"):]) == TOKEN


def test_an_invalid_token_saves_nothing(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": "ghp_WRONGWRONG0000"})
    assert status == 422 and "rejected" in res["error"] and load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_a_github_target_without_a_token_or_sign_in_is_refused(brain, fake, make_source):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft(make_source("docs", {"a.md": "A"}), destination=GH)})
    assert status == 422 and load_config(brain).pipelines == []


def test_a_directory_target_is_saved_and_shown_on_the_home_card(brain, make_source, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(make_source("docs", {"a.md": "A"}), destination={"type": "directory", "path": str(vault)})})
        _, state = ui.get("/api/state")
    row = state["pipelines"][0]
    assert row["destination"]["type"] == "directory" and row["destination"]["path"] == str(vault) and "target" not in state


def test_a_github_card_shows_the_repo_branch_and_last_commit_link_but_no_secret(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN, "sync": True})
        _, state = ui.get("/api/state")
    dest = state["pipelines"][0]["destination"]
    assert dest["repo"] == "acme/brain" and dest["branch"] == "main" and dest["last_commit"]["url"].endswith(fake.head())
    assert TOKEN not in json.dumps(state) and "credential" not in dest


def test_the_preview_of_a_saved_github_pipeline_reads_the_repository(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN})
        _, pv = ui.post("/api/preview", {"draft": draft(src, destination=load_config(brain).get("p").destination), "editing": "p"})
    assert pv["preview"]["counts"]["create"] == 2 and not [r for r in fake.requests if r[0] in ("POST", "PATCH")]


def test_the_preview_of_a_new_github_pipeline_can_use_a_tested_token_without_saving_it(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    with running_ui(brain) as ui:
        _, tested = ui.post("/api/target/test", {"destination": GH, "token": TOKEN})
        _, pv = ui.post("/api/preview", {"draft": draft(src, destination={**GH, "pending": tested["pending"]})})
    assert pv["preview"]["counts"]["create"] == 2 and vault_mod.default_vault().list() == []


def test_the_preview_of_a_new_github_pipeline_without_a_token_asks_for_the_test(brain, fake, make_source):
    with running_ui(brain) as ui:
        _, pv = ui.post("/api/preview", {"draft": draft(make_source("docs", {"a.md": "A"}), destination=GH)})
    assert pv["target_pending"] is True and pv["validation"]["errors"] == [] and pv["validation"]["can_save"] is True


def test_replace_and_remove_token_from_the_target_settings(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN})
        [secret] = vault_mod.default_vault().list()
        fake.token = "ghp_ROTATED000000000"
        status, res = ui.post("/api/pipelines/p/token", {"token": "ghp_WRONGWRONG0000"})
        assert status == 422 and vault_mod.default_vault().get(secret.id) == TOKEN
        status, res = ui.post("/api/pipelines/p/token", {"token": "ghp_ROTATED000000000"})
        assert status == 200 and vault_mod.default_vault().get(secret.id) == "ghp_ROTATED000000000" and "ghp_ROTATED" not in json.dumps(res)
        status, res = ui.request("DELETE", "/api/pipelines/p/token")
        assert status == 200 and vault_mod.default_vault().list() == [] and not load_config(brain).get("p").destination["credential"]


def test_the_vault_view_lists_names_kinds_users_and_never_values(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN})
        status, res = ui.get("/api/vault")
    assert status == 200 and res["secrets"][0]["used_by"] == ["p"] and res["secrets"][0]["kind"] == "github-token"
    assert TOKEN not in json.dumps(res) and "value" not in res["secrets"][0] and res["locked"] is False


def test_without_a_keychain_a_pipeline_is_saved_with_no_passphrase_and_the_token_goes_to_the_user_only_file(brain, fake, make_source, app_data):
    app_data.keychain.available = False
    with running_ui(brain) as ui:
        _, res = ui.get("/api/vault")
        assert res["locked"] is False and res["exists"] is False
        status, res = ui.post("/api/pipelines", {"draft": draft(make_source("docs", {"a.md": "A"}), destination=GH), "token": TOKEN})
        assert status == 200, res
        _, res = ui.get("/api/vault")
    assert res["protection"] == "file" and res["locked"] is False and TOKEN not in json.dumps(res)


def test_an_old_passphrase_vault_is_reported_locked_and_the_passphrase_unlocks_it_once(brain, fake, make_source, app_data):
    from test_secrets_file import legacy_vault

    app_data.keychain.available = False
    legacy_vault(app_data, "a long enough passphrase")
    with running_ui(brain) as ui:
        _, res = ui.get("/api/vault")
        assert res["locked"] is True and res["needs_passphrase"] is True
        status, res = ui.post("/api/vault/unlock", {"passphrase": "wrong"})
        assert status == 409
        status, res = ui.post("/api/vault/unlock", {"passphrase": "a long enough passphrase"})
        assert status == 200
        _, res = ui.get("/api/vault")
        assert res["locked"] is False and res["protection"] == "file"


def test_removing_a_secret_in_use_is_refused_by_the_api(brain, fake, make_source):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN})
        [secret] = vault_mod.default_vault().list()
        status, res = ui.post("/api/vault/remove", {"id": secret.id})
    assert status == 409 and "p" in res["error"] and vault_mod.default_vault().has(secret.id)


def test_a_needs_you_card_for_a_locked_vault_names_the_pipeline(brain, fake, make_source, app_data):
    src = make_source("docs", {"a.md": "A"})
    with running_ui(brain) as ui:
        ui.post("/api/pipelines", {"draft": draft(src, destination=GH), "token": TOKEN})
        app_data.keychain.lose()
        ui.post("/api/pipelines/p/sync")
        _, act = ui.get("/api/activity")
    [card] = act["needs_you"]
    assert card["pipeline"] == "p" and card["category"] == "vault-locked"
