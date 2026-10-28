"""MD-143: the branches of a repository as a list (read-only; local; the names are never sent anywhere else)."""
import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, uitargets, usage_events
from mdpipeline.github_api import GitHubApi
from uihelper import running_ui

TOKEN = "ghp_BRANCHLIST0123456789"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"wiki/w.md": b"W"})
    f.add_branch("md-sync")
    f.add_branch("dev")
    f.protect("main")
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


def body(**extra):
    return {"destination": {"type": "github", "repo": "acme/brain", "auth": "token"}, "token": TOKEN, **extra}


def test_the_api_lists_branch_names_with_their_protection(fake):
    assert [(b["name"], b["protected"]) for b in GitHubApi(TOKEN).branches("acme/brain")] == [("dev", False), ("main", True), ("md-sync", False)]


def test_the_picker_data_puts_the_default_branch_first_and_marks_it(fake, brain):
    res = uitargets.list_branches(brain, body())
    assert res["ok"] is True and res["default"] == "main"
    assert [b["name"] for b in res["branches"]] == ["main", "dev", "md-sync"]
    assert [b["name"] for b in res["branches"] if b["default"]] == ["main"]
    assert next(b for b in res["branches"] if b["name"] == "main")["protected"] is True


def test_the_list_is_read_only_and_never_returns_the_token(fake, brain):
    res = uitargets.list_branches(brain, body())
    assert TOKEN not in str(res)
    assert [r for r in fake.requests if r[0] not in ("GET",)] == []


def test_a_bad_token_or_repository_says_so_instead_of_listing(fake, brain):
    bad_token = uitargets.list_branches(brain, body(token="nope"))
    missing = uitargets.list_branches(brain, {"destination": {"type": "github", "repo": "acme/other", "auth": "token"}, "token": TOKEN})
    assert bad_token["ok"] is False and bad_token["errors"][0]["message"]
    assert missing["ok"] is False and missing["errors"][0]["message"]


def test_a_malformed_repository_name_is_refused_before_any_request(fake, brain):
    res = uitargets.list_branches(brain, {"destination": {"type": "github", "repo": "not a repo", "auth": "token"}, "token": TOKEN})
    assert res["ok"] is False and fake.requests == []


def test_the_endpoint_answers_for_the_ui(fake, brain):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/github/branches", body())
    assert status == 200 and res["ok"] is True and res["default"] == "main"


def test_a_folder_target_has_no_branches(brain):
    res = uitargets.list_branches(brain, {"destination": {"type": "directory", "path": ""}})
    assert res["ok"] is False


def test_branch_pick_is_a_fixed_list_method_event_with_no_name():
    event = usage_events.EVENTS["branch_pick"]
    assert list(event.props) == ["method"] and set(event.props["method"].values) == {"picker", "typed"}
