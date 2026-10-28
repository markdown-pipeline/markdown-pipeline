"""MD-152: the first run can write to the team's GitHub repo instead of a folder (the editor's sign-in and repo list, no new GitHub features)."""
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import firstrun, github_api, github_signin as gs
from mdpipeline.config import load_config
from mdpipeline.usage_events import EVENTS, sanitize
from uihelper import running_ui

CLIENT_ID = "Iv1.testclient"


@pytest.fixture
def fake(app_data):
    f = FakeGitHub(repo="acme/team-docs", branch="main", token="pat", files={"README.md": b"hello"})
    f.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    f.install_app(["acme/team-docs"])
    github_api.set_transport(f)
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")
    yield f
    github_api.set_transport(None)


@pytest.fixture
def notes(make_source):
    return make_source("team-wiki", {f"n{i}.md": f"# Note {i}\nbody {i}\n" for i in range(4)})


def sign_in(ui, fake):
    _, start = ui.post("/api/signin/start", {})
    fake.authorize_device()
    _, done = ui.post("/api/signin/poll", {"flow": start["flow"]})
    return done


def body(notes, done, **extra):
    return {"source": "folder", "path": str(notes), "target": "github", "repo": "acme/team-docs", "branch": "main", "gh_pending": done["pending"], **extra}


def test_review_to_github_counts_the_files_names_the_repo_and_commits_nothing(brain, fake, notes):
    head = fake.head()
    with running_ui(brain) as ui:
        done = sign_in(ui, fake)
        _, res = ui.post("/api/first-run/review", body(notes, done))
    assert res["ok"] is True and res["count"] == 4 and res["target"] == "github"
    assert res["dest"]["repo"] == "acme/team-docs" and res["dest"]["branch"] == "main"
    assert fake.head() == head and load_config(brain).pipelines == []


def test_write_to_github_commits_the_files_with_the_default_file_names_and_reports_the_repo(brain, fake, notes):
    with running_ui(brain) as ui:
        done = sign_in(ui, fake)
        status, res = ui.post("/api/first-run/write", body(notes, done))
    assert status == 200 and res["ok"] is True and res["files"] == 4 and res["target"] == "github"
    assert res["repo"] == "acme/team-docs" and res["repo_url"] == "https://github.com/acme/team-docs"
    pushed = [p for p in fake.files() if p.endswith(".md") and p != "README.md" and "/.mdpipeline" not in p and not p.startswith(".mdpipeline")]
    assert len(pushed) == 4
    import re

    assert all(re.match(r"\d{4}-\d{2}-\d{2}_n\d\.md$", p.rsplit("/", 1)[-1]) for p in pushed), pushed          # MD-148: date-name
    dest = load_config(brain).get("team-wiki").destination
    assert dest["type"] == "github" and dest["repo"] == "acme/team-docs" and dest["auth"] == "sign-in"
    assert "pending" not in json.dumps(dest) and "ghu_" not in json.dumps(res)


def test_a_repository_the_app_is_not_installed_on_is_a_plain_message_and_writes_nothing(brain, fake, notes):
    head = fake.head()
    with running_ui(brain) as ui:
        done = sign_in(ui, fake)
        _, res = ui.post("/api/first-run/review", body(notes, done, repo="acme/not-installed"))
    assert res["ok"] is False and res["errors"] and all(e["message"] for e in res["errors"])
    assert fake.head() == head


def test_the_folder_choice_is_unchanged_when_no_target_is_sent(brain, notes, tmp_path):
    with running_ui(brain) as ui:
        _, res = ui.post("/api/first-run/review", {"source": "folder", "path": str(notes), "dest": str(tmp_path / "out")})
    assert res["ok"] is True and res.get("target", "directory") == "directory" and res["dest"]["path"] == str(tmp_path / "out")


def test_the_first_run_step_event_may_say_which_target_and_nothing_else_about_it():
    assert "target" in EVENTS["onboarding_step_completed"].props
    ok = sanitize("onboarding_step_completed", {"step": "preview", "ms_on_step": 1200, "target": "github"})
    assert ok == {"step": "preview", "ms_on_step": 1200, "target": "github"}
    leaked = sanitize("onboarding_step_completed", {"step": "preview", "ms_on_step": 1200, "target": "acme/team-docs", "repo": "acme/team-docs"})
    assert "target" not in leaked and "repo" not in leaked          # a repo name is never an allowed value or property


def test_the_github_choice_is_offered_only_when_sign_in_is_set_up(brain, fake):
    with running_ui(brain) as ui:
        assert ui.get("/api/state")[1]["github_app"]["configured"] is True
        gs.save_app_settings("", "")
        assert ui.get("/api/state")[1]["github_app"]["configured"] is False
