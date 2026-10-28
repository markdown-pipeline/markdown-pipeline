"""MD-61/MD-65: the GitHub REST client: error mapping, https + host rule, and no token in any message."""
import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api
from mdpipeline.github_api import GitHubApi
from mdpipeline.target import (ConcurrentChange, TargetAuthError, TargetError, TargetProtected, TargetRateLimited,
                               TargetUnavailable)

TOKEN = "ghp_SECRETSECRETSECRET"


@pytest.fixture
def fake():
    return FakeGitHub(repo="acme/brain", branch="main", token=TOKEN, files={"raw/a.md": b"A"})


@pytest.fixture
def api(fake):
    return GitHubApi(TOKEN, transport=fake)


def test_user_repo_and_branch_reads(api, fake):
    assert api.user() == "tester"
    assert api.repo("acme/brain")["permissions"]["push"] is True
    b = api.branch("acme/brain", "main")
    assert b["commit"]["sha"] == fake.head()


def test_bad_token_is_an_auth_error_without_the_token_in_the_message(fake):
    bad = GitHubApi("ghp_WRONGWRONGWRONG", transport=fake)
    with pytest.raises(TargetAuthError) as exc:
        bad.user()
    assert "ghp_WRONG" not in str(exc.value) and exc.value.category == "auth"


def test_missing_repo_is_a_clear_error(api):
    with pytest.raises(TargetError) as exc:
        api.repo("nobody/nothing")
    assert "nobody/nothing" in str(exc.value) and "not found" in str(exc.value).lower()


def test_rate_limit_is_its_own_category(api, fake):
    fake.rate_limit(times=1)
    with pytest.raises(TargetRateLimited) as exc:
        api.user()
    assert exc.value.category == "rate-limited"


def test_server_error_is_a_network_category_error(api, fake):
    fake.fail("GET", "/user", status=502, times=1)
    with pytest.raises(TargetUnavailable):
        api.user()


def test_transport_exception_is_a_network_error_without_details_of_headers():
    def boom(method, url, headers, body):
        raise OSError("connection reset")

    with pytest.raises(TargetUnavailable) as exc:
        GitHubApi(TOKEN, transport=boom).user()
    assert TOKEN not in str(exc.value)


def test_ref_update_conflict_protection_and_force_never_sent(api, fake):
    base = fake.head()
    commit = api.create_commit("acme/brain", "m", api.commit("acme/brain", base)["tree"]["sha"], [base])
    fake.commit_as_other({"wiki/x.md": b"x"})
    with pytest.raises(ConcurrentChange):
        api.update_ref("acme/brain", "main", commit["sha"])
    assert fake.forced_updates == 0 and all(c["body"] is None or "force" not in c["body"] or c["body"]["force"] is False for c in fake.log)


def test_protected_branch_ref_update_is_a_protection_error(api, fake):
    base = fake.head()
    commit = api.create_commit("acme/brain", "m", api.commit("acme/brain", base)["tree"]["sha"], [base])
    fake.protect("main")
    with pytest.raises(TargetProtected):
        api.update_ref("acme/brain", "main", commit["sha"])


def test_only_https_and_the_configured_host(fake):
    with pytest.raises(ValueError):
        GitHubApi(TOKEN, api_url="http://api.github.com", transport=fake)
    with pytest.raises(ValueError):
        GitHubApi(TOKEN, api_url="https://evil.example.com", transport=fake)


def test_the_real_transport_refuses_other_hosts_before_connecting():
    with pytest.raises(ValueError):
        github_api.urllib_transport("GET", "https://evil.example.com/x", {}, None)
    with pytest.raises(ValueError):
        github_api.urllib_transport("GET", "http://api.github.com/x", {}, None)


def test_blobs_round_trip_and_tree_truncation_is_an_error(api, fake):
    sha = api.create_blob("acme/brain", b"hello")
    assert api.blob("acme/brain", sha) == b"hello"
    fake.max_tree_entries = 0
    tree = api.commit("acme/brain", fake.head())["tree"]["sha"]
    with pytest.raises(TargetError) as exc:
        api.tree("acme/brain", tree)
    assert "too large" in str(exc.value)
