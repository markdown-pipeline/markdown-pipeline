"""MD-93: the next-sync dry run of a SAVED pipeline for view mode: same plan engine as the editor, writes nothing, failures never look like deletes."""
import re

import pytest

from conftest import tree_snapshot
from fake_github import FakeGitHub
from fake_jira import FakeJira
from mdpipeline import github_api, github_signin as gs, jira_api, vault as vault_mod
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.service import set_paused
from mdpipeline.targetconfig import GitHubSpec
from test_jira_sync import EMAIL, SITE, TOKEN as JIRA_TOKEN, jira_source
from uihelper import running_ui

GH_TOKEN = "ghp_PLANVIEWSECRET0123456789"


def plan(ui, name="p"):
    return ui.get(f"/api/pipelines/{name}/plan")


@pytest.fixture
def folders(brain, add_pipeline, make_source):
    a = make_source("docs", {f"{i}.md": f"note {i}" for i in range(6)})
    add_pipeline(Pipeline.new("p", [Source(str(a), "docs")], name_policy="original"))
    return a


def test_the_plan_of_a_saved_pipeline_counts_what_the_next_sync_would_do(brain, folders):
    sync_pipeline(brain, "p")
    (folders / "0.md").write_text("changed")
    (folders / "new.md").write_text("new")
    (folders / "1.md").unlink()
    with running_ui(brain) as ui:
        status, res = plan(ui)
    c = res["preview"]["counts"]
    assert status == 200 and (c["create"], c["update"], c["delete"]) == (1, 1, 1)
    assert res["preview"]["guard"] is not None and res["preview"]["groups"][0]["label"] == "docs"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", res["planned_at"])


def test_the_plan_is_the_editors_preview_of_the_saved_config(brain, folders):
    from mdpipeline.config import load_config
    from mdpipeline.uieditor import pipeline_to_draft

    with running_ui(brain) as ui:
        _, viewed = plan(ui)
        _, edited = ui.post("/api/preview", {"draft": pipeline_to_draft(load_config(brain).get("p")), "editing": "p"})
    assert viewed["preview"] == edited["preview"] and viewed["validation"] == edited["validation"]


def test_unknown_pipeline_is_not_found_and_there_is_no_write_route(brain, folders):
    with running_ui(brain) as ui:
        assert plan(ui, "nope")[0] == 404
        for method in ("POST", "PUT", "DELETE"):
            assert ui.request(method, "/api/pipelines/p/plan", {})[0] == 404


def test_planning_writes_nothing_for_a_directory_pipeline(brain, folders, app_data):
    sync_pipeline(brain, "p")
    (folders / "new.md").write_text("new")
    before = tree_snapshot(brain.root, folders)
    with running_ui(brain) as ui:
        for _ in range(3):
            assert plan(ui)[0] == 200
    assert tree_snapshot(brain.root, folders) == before


def test_a_paused_pipeline_still_shows_its_plan(brain, folders):
    set_paused(brain, "p", True, trigger="cli")
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["preview"]["counts"]["create"] == 6


# failures are never deletes ------------------------------------------------------------------------------------
def test_a_missing_source_folder_is_a_problem_with_needs_you_wording_and_no_deletes(brain, folders, tmp_path):
    sync_pipeline(brain, "p")
    folders.rename(tmp_path / "moved")
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["preview"]["counts"]["delete"] == 0 and res["preview"]["guard"]["blocked"] is False
    [group] = res["preview"]["groups"]
    assert group["status"] != "ok" and group["error"]
    [problem] = res["problems"]
    assert problem["kind"] == "source_missing" and problem["label"] == "docs" and problem["path"]      # MD-136


@pytest.fixture
def gh(brain, add_pipeline, make_source, app_data):
    fake = FakeGitHub(repo="acme/brain", branch="main", token=GH_TOKEN, files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    sid = vault_mod.default_vault().add("github-token", "GitHub token for p", GH_TOKEN)
    dest = GitHubSpec("acme/brain", f"vault:{sid}", "main").to_dict()
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [Source(str(src), "docs")], destination=dest, name_policy="original"))
    yield fake
    github_api.set_transport(None)


def test_planning_a_github_pipeline_makes_only_get_requests_and_no_commit(brain, gh):
    sync_pipeline(brain, "p")
    head, history = gh.head(), gh.history()
    gh.requests.clear()
    with running_ui(brain) as ui:
        status, res = plan(ui)
        assert plan(ui)[0] == 200
    assert status == 200 and res["preview"]["counts"]["create"] == 0 and res["preview"]["counts"]["skip"] == 2
    assert {m for m, _ in gh.requests} == {"GET"} and gh.head() == head and gh.history() == history


def test_a_rejected_github_credential_is_a_problem_not_a_plan_of_deletes(brain, gh):
    sync_pipeline(brain, "p")
    vault_mod.default_vault().replace(vault_mod.default_vault().list()[0].id, "ghp_WRONGWRONG0000")
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["target_error"]["category"] == "auth"
    assert res["preview"]["counts"]["all"] == 0 and res["preview"]["counts"]["delete"] == 0
    [problem] = res["problems"]
    assert problem["kind"] == "target" and problem["category"] == "auth" and "token" in problem["next_step"]
    assert "ghp_WRONG" not in str(res)


def test_a_locked_vault_is_a_problem_with_its_needs_you_wording(brain, gh, monkeypatch):
    def locked(*a, **k):
        raise vault_mod.VaultLocked("The vault is locked.", needs_passphrase=True)

    monkeypatch.setattr(vault_mod.Vault, "get", locked)
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["target_error"]["category"] == "vault-locked"
    [problem] = res["problems"]
    assert problem["title"] == "The vault is locked" and res["preview"]["counts"]["delete"] == 0


def test_github_unreachable_is_a_problem(brain, gh):
    gh.fail("GET", "/repos/acme/brain", status=500, times=50)
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["target_error"] and res["problems"][0]["kind"] == "target" and res["preview"]["counts"]["delete"] == 0


def test_an_expired_sign_in_says_sign_in_again_and_deletes_nothing(brain, add_pipeline, make_source, app_data):
    fake = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    fake.enable_app(client_id="Iv1.t", slug="md-pipeline-test")
    fake.install_app(["acme/brain"])
    github_api.set_transport(fake)
    gs.save_app_settings("Iv1.t", "md-pipeline-test")
    try:
        tokens = gs.SignInTokens(access_token="old", refresh_token="gone", expires_at=1.0, refresh_expires_at=1.0, login="mia")
        sid = vault_mod.default_vault().add("github-sign-in", "GitHub sign-in for p", tokens.to_json())
        dest = GitHubSpec("acme/brain", f"vault:{sid}", "main").to_dict() | {"auth": "sign-in"}
        add_pipeline(Pipeline.new("p", [Source(str(make_source("docs", {"a.md": "A"})), "docs")], destination=dest))
        with running_ui(brain) as ui:
            status, res = plan(ui)
        assert status == 200 and res["target_error"]["category"] == "sign-in-expired"
        assert res["problems"][0]["title"] == "Sign in with GitHub again" and res["preview"]["counts"]["delete"] == 0
    finally:
        github_api.set_transport(None)


# jira ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def jira(brain, add_pipeline, monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=JIRA_TOKEN)
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", JIRA_TOKEN)
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name", guard_threshold_percent=100))
    yield f
    jira_api.set_transport(None)


def test_planning_a_jira_pipeline_reads_only_and_marks_the_source_connected(brain, jira):
    sync_pipeline(brain, "p")
    snapshot = tree_snapshot(brain.root)
    jira.requests.clear()
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["preview"]["counts"]["delete"] == 0 and res["sources"]["jira"] == {"ok": True, "message": "Jira: connected"}
    assert [r for r in jira.requests if r[0] != "GET" and not r[1].endswith("approximate-count")] == []
    assert tree_snapshot(brain.root) == snapshot


def test_a_rejected_jira_token_is_a_problem_and_never_deletes(brain, jira, monkeypatch):
    sync_pipeline(brain, "p")
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN")
    with running_ui(brain) as ui:
        status, res = plan(ui)
    assert status == 200 and res["preview"]["counts"]["delete"] == 0
    assert res["sources"]["jira"]["ok"] is False and "rejected" in res["sources"]["jira"]["message"].lower()
    [problem] = res["problems"]
    assert problem["kind"] == "source" and problem["category"] == "auth" and "Jira rejected the token" == problem["title"]
    assert "ATATT-WRONG" not in str(res)
