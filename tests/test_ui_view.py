"""MD-92: pipeline view mode (backend): the full configuration of one pipeline, read-only, secrets never shown."""
import json
from types import SimpleNamespace

import pytest

from conftest import tree_snapshot
from fake_github import FakeGitHub
from mdpipeline import github_api, vault as vault_mod
from mdpipeline.config import Pipeline, Source
from mdpipeline.targetconfig import GitHubSpec
from secret_search import files_containing
from test_jira_sync import SITE, jira_source
from uihelper import running_ui

SENTINEL = "ghp_VIEWMODESECRET0123456789"


def view(ui, name="p"):
    return ui.get(f"/api/pipelines/{name}/view")


@pytest.fixture
def folder_pipeline(brain, add_pipeline, make_source):
    a = make_source("docs", {"1.md": "one", "2.md": "two", "sub/3.md": "three"})
    b = make_source("wiki", {"w.md": "w"})
    add_pipeline(Pipeline.new("p", [Source(str(a), "docs"), Source(str(b), "wiki")], include=["**/*.md"], exclude=[".git/**"],
                              on_source_delete="keep", guard_threshold_percent=25, guard_min_files=3, name_policy="date-name"))
    return a, b


def test_view_returns_every_setting_the_editor_can_set(brain, folder_pipeline):
    a, b = folder_pipeline
    with running_ui(brain) as ui:
        status, res = view(ui)
    assert status == 200
    cfg = res["config"]
    assert cfg["name"] == "p" and cfg["paused"] is False
    assert [(s["type"], s["label"], s["path"]) for s in cfg["sources"]] == [("folder", "docs", str(a)), ("folder", "wiki", str(b))]
    assert cfg["include"] == ["**/*.md"] and cfg["exclude"] == [".git/**"]
    assert cfg["target"] == "raw/{pipeline}/{source}/{relpath}"
    assert cfg["on_source_delete"] == "keep"
    assert cfg["guard"] == {"threshold_percent": 25, "min_files": 3}
    assert cfg["destination"]["type"] == "directory" and cfg["destination"]["path"] == str(brain.root)


def test_view_shows_the_file_name_policy_with_one_example_name(brain, folder_pipeline):
    with running_ui(brain) as ui:
        policy = view(ui)[1]["config"]["name_policy"]
    assert policy["policy"] == "date-name" and policy["pattern"] == ""
    assert policy["example"].endswith(".md") and policy["example"] != "meeting-notes.md"


def test_view_example_for_original_policy_is_the_file_name_itself(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("d", {"a.md": "a"}))]))
    with running_ui(brain) as ui:
        policy = view(ui)[1]["config"]["name_policy"]
    assert policy["policy"] == "original" and policy["example"] == "meeting-notes.md"


def test_view_example_for_a_custom_pattern_uses_the_pattern(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("d", {"a.md": "a"}))], name_policy="custom", name_pattern="{source}--{slug}"))
    with running_ui(brain) as ui:
        policy = view(ui)[1]["config"]["name_policy"]
    assert policy["pattern"] == "{source}--{slug}" and policy["example"] == "d--meeting-notes.md"


def test_unknown_pipeline_is_not_found(brain):
    with running_ui(brain) as ui:
        status, res = view(ui, "nope")
    assert status == 404 and "nope" in res["error"]


def test_each_folder_shows_its_live_check(brain, folder_pipeline, tmp_path):
    a, b = folder_pipeline
    b.rename(tmp_path / "moved")
    with running_ui(brain) as ui:
        sources = view(ui)[1]["config"]["sources"]
    assert sources[0]["check"] == {"ok": True, "exists": True, "md_files": 3, "message": "Folder found · 3 .md files"}
    assert sources[1]["check"]["ok"] is False and sources[1]["check"]["message"] == "Folder not found"


def test_a_paused_pipeline_says_so(brain, folder_pipeline):
    from mdpipeline.service import set_paused

    set_paused(brain, "p", True, trigger="cli")
    with running_ui(brain) as ui:
        assert view(ui)[1]["config"]["paused"] is True


def test_viewing_writes_nothing_and_there_is_no_write_route(brain, folder_pipeline, app_data):
    a, b = folder_pipeline
    before = tree_snapshot(brain.root, a, b, app_data.dir) if app_data.dir.exists() else tree_snapshot(brain.root, a, b)
    with running_ui(brain) as ui:
        for _ in range(2):
            assert view(ui)[0] == 200
        for method in ("POST", "PUT", "DELETE"):
            status, _ = ui.request(method, "/api/pipelines/p/view", {})
            assert status == 404
    after = tree_snapshot(brain.root, a, b, app_data.dir) if app_data.dir.exists() else tree_snapshot(brain.root, a, b)
    assert before == after


# jira: credentials as method + set/not set + name -----------------------------------------------------------------
def test_jira_source_shows_site_jql_label_and_credentials_without_values(brain, add_pipeline, monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    add_pipeline(Pipeline("p", [jira_source("project = MD", "tickets")]))
    with running_ui(brain) as ui:
        [src] = view(ui)[1]["config"]["sources"]
    assert (src["type"], src["label"], src["site"], src["jql"]) == ("jira", "tickets", SITE, "project = MD")
    assert src["credentials"]["email"] == {"method": "env", "name": "JIRA_EMAIL", "set": True}
    assert src["credentials"]["token"] == {"method": "env", "name": "JIRA_TOKEN", "set": False}
    assert "me@example.com" not in json.dumps(src)


def test_jira_source_with_vault_credentials_shows_the_vault_name(brain, add_pipeline):
    v = vault_mod.default_vault()
    tid = v.add("jira-token", "Jira token for p", "ATATT-SECRET-VALUE")
    eid = v.add("jira-email", "Jira email for p", "me@example.com")
    src = Source(SITE, "jira", type="jira", options={"site": SITE, "jql": "project = MD", "email_ref": f"vault:{eid}", "token_ref": f"vault:{tid}"})
    add_pipeline(Pipeline("p", [src]))
    with running_ui(brain) as ui:
        status, res = view(ui)
    creds = res["config"]["sources"][0]["credentials"]
    assert creds["token"] == {"method": "vault", "name": "Jira token for p", "set": True}
    assert creds["email"]["name"] == "Jira email for p"
    assert "ATATT-SECRET-VALUE" not in json.dumps(res) and "me@example.com" not in json.dumps(res)


def test_a_vault_reference_that_was_removed_is_not_set(brain, add_pipeline):
    src = Source(SITE, "jira", type="jira", options={"site": SITE, "jql": "x", "email_ref": "vault:gone1", "token_ref": "vault:gone2"})
    add_pipeline(Pipeline("p", [src]))
    with running_ui(brain) as ui:
        creds = view(ui)[1]["config"]["sources"][0]["credentials"]
    assert creds["token"]["set"] is False and creds["token"]["method"] == "vault"


# github target ----------------------------------------------------------------------------------------------------
@pytest.fixture
def gh_pipeline(brain, add_pipeline, make_source):
    fake = FakeGitHub(repo="acme/brain", branch="main", token=SENTINEL, files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    secret_id = vault_mod.default_vault().add("github-token", "GitHub token for p", SENTINEL)
    dest = GitHubSpec("acme/brain", f"vault:{secret_id}", "main").to_dict() | {"auth": "token", "clone": "/home/x/clone"}
    add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))], destination=dest))
    yield SimpleNamespace(secret_id=secret_id, fake=fake)
    github_api.set_transport(None)


def test_github_target_shows_repo_branch_auth_clone_and_the_credential_without_its_value(brain, gh_pipeline, app_data):
    with running_ui(brain) as ui:
        status, res = view(ui)
    dest = res["config"]["destination"]
    assert status == 200
    assert (dest["type"], dest["repo"], dest["branch"], dest["auth"], dest["clone"]) == ("github", "acme/brain", "main", "token", "/home/x/clone")
    assert dest["credential"] == {"method": "vault", "name": "GitHub token for p", "set": True}
    assert SENTINEL not in json.dumps(res) and files_containing(SENTINEL, brain.root) == []


def test_github_view_makes_only_get_requests_to_github(brain, gh_pipeline):
    with running_ui(brain) as ui:
        assert view(ui)[0] == 200
    assert {m for m, _ in gh_pipeline.fake.requests} <= {"GET"}      # status reads the repository head, like the home does


def test_a_locked_vault_is_reported_not_crashed(brain, gh_pipeline, app_data, monkeypatch):
    def locked(*a, **k):
        raise vault_mod.VaultLocked("locked", needs_passphrase=True)

    monkeypatch.setattr(vault_mod.Vault, "list", locked)
    with running_ui(brain) as ui:
        status, res = view(ui)
    assert status == 200
    cred = res["config"]["destination"]["credential"]
    assert cred["method"] == "vault" and cred["set"] is None and cred["locked"] is True
