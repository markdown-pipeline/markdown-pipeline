"""MD-83 / MD-84: Sign in with GitHub (GitHub App device flow) against the fake: no client secret, tokens only in the vault, refresh, Sign in again."""
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, github_signin as gs, vault as vault_mod
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.targetconfig import GitHubSpec, test_connection as check_connection
from secret_search import files_containing, repo_containing

CLIENT_ID = "Iv1.testclient"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    f.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    f.install_app(["acme/brain"])
    github_api.set_transport(f)
    yield f
    github_api.set_transport(None)


@pytest.fixture(autouse=True)
def configured(app_data):
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")


def sign_in(fake) -> gs.SignInTokens:
    code = gs.request_device_code(CLIENT_ID)
    fake.authorize_device()
    return gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)


# --- the device flow ---------------------------------------------------------------------------------------------------
def test_the_device_code_has_a_user_code_a_link_and_an_interval(fake):
    code = gs.request_device_code(CLIENT_ID)
    assert code.user_code == "WDJB-MJHT" and code.verification_uri == "https://github.com/login/device" and code.interval == 5 and code.expires_in == 900


def test_an_app_that_is_not_registered_yet_says_what_to_do(fake, app_data):
    gs.save_app_settings("", "")
    with pytest.raises(gs.SignInNotConfigured) as exc:
        gs.require_client_id()
    assert "github-app-registration" in str(exc.value)


def test_device_flow_disabled_in_the_app_settings_is_explained(fake):
    fake.device_flow_enabled = False
    with pytest.raises(gs.SignInError) as exc:
        gs.request_device_code(CLIENT_ID)
    assert "Device Flow" in str(exc.value)


def test_waiting_polls_at_the_interval_until_the_person_approves(fake):
    code = gs.request_device_code(CLIENT_ID)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            fake.authorize_device()

    tokens = gs.wait_for_tokens(CLIENT_ID, code, sleep=sleep)
    assert sleeps == [5, 5, 5] and tokens.access_token.startswith("ghu_") and tokens.refresh_token.startswith("ghr_")


def test_slow_down_makes_the_next_waits_longer(fake):
    code = gs.request_device_code(CLIENT_ID)
    fake.slow_down(1)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            fake.authorize_device()

    gs.wait_for_tokens(CLIENT_ID, code, sleep=sleep)
    assert sleeps == [5, 10]


def test_a_denied_request_stops_with_a_plain_message(fake):
    code = gs.request_device_code(CLIENT_ID)
    fake.deny_device()
    with pytest.raises(gs.SignInDenied):
        gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)


def test_an_expired_code_asks_to_start_again(fake):
    code = gs.request_device_code(CLIENT_ID)
    fake.expire_device_code()
    with pytest.raises(gs.SignInError) as exc:
        gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)
    assert "start" in str(exc.value).lower()


def test_waiting_gives_up_when_the_code_runs_out_of_time(fake):
    code = gs.request_device_code(CLIENT_ID)
    clock = iter(range(0, 10_000, 400))
    with pytest.raises(gs.SignInError) as exc:
        gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None, clock=lambda: next(clock))
    assert "expired" in str(exc.value).lower() or "start" in str(exc.value).lower()


def test_tokens_know_when_they_expire(fake):
    tokens = gs.SignInTokens.from_response({"access_token": "a", "expires_in": 28800, "refresh_token": "r", "refresh_token_expires_in": 100}, now=1000.0)
    assert tokens.expires_at == 29800.0 and tokens.refresh_expires_at == 1100.0
    assert gs.SignInTokens.from_json(tokens.to_json()) == tokens
    forever = gs.SignInTokens.from_response({"access_token": "a"}, now=1000.0)
    assert forever.expires_at is None and not forever.needs_refresh(now=10**12)


# --- refresh (no client secret) -----------------------------------------------------------------------------------------
def test_refresh_works_without_a_client_secret_and_rotates_the_refresh_token(fake):
    tokens = sign_in(fake)
    fake.expire_access_tokens()
    fresh = gs.refresh_tokens(CLIENT_ID, tokens)
    assert fresh.access_token != tokens.access_token and fresh.refresh_token != tokens.refresh_token
    assert "client_secret" not in fake.sign_in_log[-1]["form"]


def test_a_refresh_github_refuses_means_sign_in_again(fake):
    fake.refresh_requires_secret = True
    tokens = sign_in(fake)
    with pytest.raises(gs.SignInAgain) as exc:
        gs.refresh_tokens(CLIENT_ID, tokens)
    assert "sign in again" in str(exc.value).lower() and exc.value.category == "sign-in-expired"


def test_the_network_rule_two_login_endpoints_only_over_https(fake):
    sign_in(fake)
    assert {e["path"] for e in fake.sign_in_log} <= {"/login/device/code", "/login/oauth/access_token"}
    with pytest.raises(ValueError):
        gs._post("https://evil.example.com/login/oauth/access_token", {})
    with pytest.raises(ValueError):
        gs._post("http://github.com/login/oauth/access_token", {})
    with pytest.raises(ValueError):
        gs._post("https://github.com/other", {})


# --- the credential used by the GitHub client ------------------------------------------------------------------------------
def store(fake, tokens=None):
    tokens = tokens or sign_in(fake)
    tokens.login = "tester"
    return vault_mod.default_vault().add("github-sign-in", "GitHub sign-in of tester", tokens.to_json())


def test_a_valid_token_is_used_without_any_login_request(fake):
    secret_id = store(fake)
    seen = len(fake.sign_in_log)
    assert gs.SignInCredential(secret_id, CLIENT_ID).token().startswith("ghu_") and len(fake.sign_in_log) == seen


def test_an_expiring_token_is_refreshed_before_use_and_the_new_tokens_replace_the_old_in_the_vault(fake):
    tokens = sign_in(fake)
    tokens.expires_at = 1.0                                 # long past
    secret_id = store(fake, tokens)
    token = gs.SignInCredential(secret_id, CLIENT_ID).token()
    stored = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id))
    assert token == stored.access_token != tokens.access_token and stored.refresh_token != tokens.refresh_token


def test_a_failed_refresh_raises_sign_in_again_and_keeps_the_stored_tokens(fake):
    tokens = sign_in(fake)
    tokens.expires_at = 1.0
    secret_id = store(fake, tokens)
    fake.revoke_tokens()
    with pytest.raises(gs.SignInAgain):
        gs.SignInCredential(secret_id, CLIENT_ID).token()
    assert gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id)).access_token == tokens.access_token


def test_a_refresh_that_lost_a_race_uses_the_tokens_another_process_stored(fake):
    tokens = sign_in(fake)
    tokens.expires_at = 1.0
    secret_id = store(fake, tokens)
    fresh = gs.refresh_tokens(CLIENT_ID, tokens)                       # another process refreshed first and stored its result
    fresh.login = "tester"
    vault_mod.default_vault().replace(secret_id, fresh.to_json())
    cred = gs.SignInCredential(secret_id, CLIENT_ID)
    reads = iter([tokens, fresh])                                      # this process read the old, now burnt, refresh token just before the other one stored its result
    cred._stored = lambda: next(reads)
    assert cred.token() == fresh.access_token


# --- a pipeline with a sign-in target ------------------------------------------------------------------------------------
def signin_pipeline(brain, make_source, secret_id, name="p", files=None):
    cfg = load_config(brain)
    dest = GitHubSpec("acme/brain", f"vault:{secret_id}", "main").to_dict() | {"auth": "sign-in"}
    cfg.pipelines.append(Pipeline.new(name, [str(make_source(name + "src", files or {"a.md": "A"}))], destination=dest))
    save_config(brain, cfg)


def test_a_sync_commits_with_the_sign_in_token_and_the_token_is_only_in_the_vault(brain, fake, make_source, app_data):
    secret_id = store(fake)
    signin_pipeline(brain, make_source, secret_id)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and fake.read("raw/p/psrc/a.md")
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id))
    for secret in (tokens.access_token, tokens.refresh_token):
        assert files_containing(secret, brain.root, app_data.dir) == [] and repo_containing(secret, fake) == []


def test_a_token_that_expires_mid_sync_is_renewed_and_the_same_sync_completes(brain, fake, make_source):
    secret_id = store(fake)
    signin_pipeline(brain, make_source, secret_id)
    fake.before("PATCH", fake.expire_access_tokens, times=1)         # expires right before the commit point (MD-187: renewed once, then retried)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and fake.read("raw/p/psrc/a.md")


def test_a_sign_in_revoked_mid_sync_leaves_the_branch_unchanged_and_says_sign_in_again(brain, fake, make_source):
    secret_id = store(fake)
    signin_pipeline(brain, make_source, secret_id)
    head = fake.head()
    fake.before("PATCH", fake.revoke_tokens, times=1)                # the person revoked the app right before the commit point
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "sign-in-expired" and fake.head() == head and fake.read("raw/p/psrc/a.md") is None


def test_a_revoked_sign_in_writes_and_deletes_nothing_and_says_sign_in_again(brain, fake, make_source):
    secret_id = store(fake)
    signin_pipeline(brain, make_source, secret_id)
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    before = fake.files()
    fake.revoke_tokens()
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id))
    tokens.expires_at = 1.0
    vault_mod.default_vault().replace(secret_id, tokens.to_json())
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and res.category == "sign-in-expired" and "sign in again" in res.message.lower()
    assert fake.files() == before


def test_the_app_uninstalled_is_reported_like_a_revoked_sign_in_never_as_a_deletion(brain, fake, make_source):
    secret_id = store(fake)
    signin_pipeline(brain, make_source, secret_id)
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    before = fake.files()
    fake._installed.clear()                                          # the app was uninstalled: the repository is no longer visible to the token
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and fake.files() == before


# --- test connection ---------------------------------------------------------------------------------------------------
def api_for_tokens(tokens):
    return github_api.GitHubApi(tokens.access_token)


def test_test_connection_with_sign_in_reports_the_user_and_the_commit_author(fake):
    spec = GitHubSpec("acme/brain", "vault:x", "main", auth="sign-in")
    conn = check_connection(spec, api_for_tokens(sign_in(fake)))
    assert conn.can_write and conn.user == "tester" and not conn.problems


def test_an_app_not_installed_on_the_repository_gets_the_install_link(fake):
    fake._installed.clear()
    spec = GitHubSpec("acme/brain", "vault:x", "main", auth="sign-in")
    with pytest.raises(Exception) as exc:
        check_connection(spec, api_for_tokens(sign_in(fake)))
    assert "https://github.com/apps/md-pipeline-test/installations/new" in str(exc.value) and "not installed" in str(exc.value).lower()


def test_an_installation_without_contents_write_is_not_a_writable_target(fake):
    fake.set_permission("read")
    spec = GitHubSpec("acme/brain", "vault:x", "main", auth="sign-in")
    conn = check_connection(spec, api_for_tokens(sign_in(fake)))
    assert not conn.can_write and any("write" in p.lower() for p in conn.problems)


def test_the_installed_repositories_are_listed_for_the_picker(fake):
    fake.install_app(["acme/brain", "acme/other"])
    assert gs.installed_repos(api_for_tokens(sign_in(fake))) == ["acme/brain", "acme/other"]
