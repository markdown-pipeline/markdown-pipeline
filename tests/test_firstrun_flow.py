"""MD-159 backend: the time window of the project query, the destination check, the new usage step and property, folder -> folder needs no vault."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, jira_picker, usage_events
from mdpipeline.usage_events import EVENTS
from test_jira_sync import EMAIL, TOKEN
from uihelper import running_ui


# --- AC3: the time window is built (build-jql takes it) ---------------------------------------------------------------
@pytest.mark.parametrize("keys,days,jql", [
    (["BB", "AA"], 30, "project in (AA, BB) AND updated >= -30d ORDER BY updated DESC"),
    (["AA"], 90, "project in (AA) AND updated >= -90d ORDER BY updated DESC"),
    (["AA"], None, "project in (AA) ORDER BY updated DESC"),
    ([], 90, "updated >= -90d ORDER BY updated DESC"),
    ([], 30, "updated >= -30d ORDER BY updated DESC"),
    ([], None, "ORDER BY updated DESC"),
])
def test_the_query_takes_projects_and_a_time_window(keys, days, jql):
    assert jira_picker.build_jql(keys, days=days, window=True) == jql


def test_without_a_window_an_empty_pick_is_still_refused():
    with pytest.raises(ValueError):
        jira_picker.build_jql([])


def test_a_window_that_is_not_30_90_or_all_time_is_refused():
    with pytest.raises(ValueError):
        jira_picker.build_jql(["AA"], days=7, window=True)


def test_the_endpoint_takes_a_window_and_no_projects(brain):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/jira/build-jql", {"keys": [], "days": 30})
        assert status == 200 and res["jql"] == "updated >= -30d ORDER BY updated DESC"
        status, res = ui.post("/api/jira/build-jql", {"keys": ["MD", "MP"], "days": None})
        assert res["jql"] == "project in (MD, MP) ORDER BY updated DESC"
        status, _ = ui.post("/api/jira/build-jql", {"keys": []})
        assert status == 400


# --- the destination check: the suggested folder and a live writable check ----------------------------------------------
def test_dest_check_suggests_the_default_folder_for_a_folder_source(brain, tmp_path):
    src = tmp_path / "team-wiki"
    src.mkdir()
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/dest-check", {"source": "folder", "path": str(src), "dest": ""})
    assert res["suggested"] == "~/markdown-pipeline/team-wiki" and res["ok"] is True and res["exists"] is False


def test_dest_check_suggests_the_site_name_for_jira(brain):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/dest-check", {"source": "jira", "site": "acme", "dest": ""})
    assert res["suggested"] == "~/markdown-pipeline/acme-jira"


def test_dest_check_says_an_existing_folder_exists_and_a_new_one_will_be_created(brain, tmp_path):
    src = tmp_path / "notes"
    src.mkdir()
    (tmp_path / "out").mkdir()
    with running_ui(brain) as ui:
        _, existing = ui.post("/api/first-run/dest-check", {"source": "folder", "path": str(src), "dest": str(tmp_path / "out")})
        _, fresh = ui.post("/api/first-run/dest-check", {"source": "folder", "path": str(src), "dest": str(tmp_path / "new" / "deep")})
    assert (existing["ok"], existing["exists"]) == (True, True) and (fresh["ok"], fresh["exists"]) == (True, False)


def test_dest_check_refuses_a_folder_that_cannot_be_written(brain, tmp_path):
    src = tmp_path / "notes"
    src.mkdir()
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/dest-check", {"source": "folder", "path": str(src), "dest": str(blocker / "sub")})
    assert res["ok"] is False and "file" in res["reason"]


# --- AC11: the usage step and property --------------------------------------------------------------------------------
def test_choose_scope_is_an_allowed_step():
    assert usage_events.STEP.check("choose_scope") == "choose_scope"


def test_completing_a_step_can_carry_dest_changed_as_a_flag_and_no_path():
    event = EVENTS["onboarding_step_completed"]
    assert "dest_changed" in event.props
    kept = usage_events.sanitize("onboarding_step_completed", {"step": "choose_target", "ms_on_step": 1000, "target": "directory", "dest_changed": True, "path": "/home/x"})
    assert kept.get("dest_changed") is True and "path" not in kept


# --- AC7 / P6: a folder -> folder write never needs the vault, even with no keychain -----------------------------------
def test_a_folder_to_folder_write_succeeds_on_a_machine_with_no_keychain_and_no_passphrase(brain, tmp_path, app_data):
    app_data.keychain.available = False
    src = tmp_path / "notes"
    src.mkdir()
    (src / "a.md").write_text("# A\n")
    with running_ui(brain) as ui:
        status, res = ui.post("/api/first-run/write", {"source": "folder", "path": str(src), "dest": str(tmp_path / "out")})
    assert status == 200 and res["ok"] is True and res["files"] == 1
    assert not (app_data.dir / "vault.json").exists()


# --- a Jira source with a GitHub target (never exercised before MD-159; the write failed on the Jira token's placeholder) -----------
def test_write_a_jira_source_to_a_github_repo_keeps_both_secrets_in_the_vault_and_commits(brain, app_data):
    from fake_github import FakeGitHub
    from mdpipeline import github_api, github_signin as gs
    from mdpipeline.config import load_config

    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_issue("ACME", "Issue one")
    jira_api.set_transport(f, sleep=lambda s: None)
    hub = FakeGitHub(repo="acme/team-docs", branch="main", token="pat", files={"README.md": b"hello"})
    hub.enable_app(client_id="Iv1.testclient", slug="md-pipeline-test")
    hub.install_app(["acme/team-docs"])
    github_api.set_transport(hub)
    gs.save_app_settings("Iv1.testclient", "md-pipeline-test")
    try:
        with running_ui(brain) as ui:
            _, start = ui.post("/api/signin/start", {})
            hub.authorize_device()
            _, done = ui.post("/api/signin/poll", {"flow": start["flow"]})
            _, tested = ui.post("/api/first-run/jira-test", {"site": "acme", "email": EMAIL, "token": TOKEN})
            status, res = ui.post("/api/first-run/write", {"source": "jira", "site": "acme", "email": EMAIL, "pending": tested["pending"], "target": "github",
                                                          "repo": "acme/team-docs", "branch": "main", "gh_pending": done["pending"]})
    finally:
        github_api.set_transport(None)
        jira_api.set_transport(None)
    assert status == 200 and res["ok"] is True and res["files"] == 1 and res["repo"] == "acme/team-docs"
    config = (brain.config_dir / "config.yaml").read_text()
    assert TOKEN not in config and "vault:" in config
    assert load_config(brain).get("acme-jira").destination["type"] == "github"


def test_what_we_send_says_the_folder_or_github_choice_and_the_changed_flag_never_the_path():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "docs" / "what-we-send.md").read_text().replace("\n", " ")
    assert "whether you chose a folder or GitHub and whether you changed the suggested folder (yes or no, never the path)" in text
