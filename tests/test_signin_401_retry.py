"""MD-187 AC2-AC4: a 401 on a sign-in pipeline renews the sign-in once and retries before any error; a refused renewal says "sign in again"."""
import threading
import time

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, github_signin as gs, vault as vault_mod
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.targetconfig import GitHubSpec

CLIENT_ID = "Iv1.testclient"
HOUR = 3600.0


@pytest.fixture
def world(brain, make_source, app_data, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    fake = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    fake.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    fake.install_app(["acme/brain"])
    fake.clock = lambda: now[0]
    github_api.set_transport(fake)
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")
    code = gs.request_device_code(CLIENT_ID)
    fake.authorize_device()
    tokens = gs.wait_for_tokens(CLIENT_ID, code, sleep=lambda s: None)
    tokens.login = "tester"
    secret_id = vault_mod.default_vault().add("github-sign-in", "tester", tokens.to_json())
    cfg = load_config(brain)
    dest = GitHubSpec("acme/brain", f"vault:{secret_id}", "main").to_dict() | {"auth": "sign-in"}
    cfg.pipelines.append(Pipeline.new("p", [str(make_source("src", {"a.md": "A"}))], destination=dest))
    save_config(brain, cfg)
    yield now, fake, brain, secret_id
    github_api.set_transport(None)


def refreshes(fake) -> int:
    return sum(1 for entry in fake.sign_in_log if entry["form"].get("grant_type") == "refresh_token")


def test_a_token_that_expires_during_a_sync_is_renewed_once_and_the_sync_is_ok(world):
    now, fake, brain, secret_id = world
    now[0] += 8 * HOUR - 130                                   # outside the 120 s refresh margin, so the old token is still used
    fake.before("PATCH", lambda: now.__setitem__(0, now[0] + 200), times=1)      # and it expires before the commit
    res = sync_pipeline(brain, "p", trigger="schedule")
    assert res.result == "ok" and fake.read("raw/p/src/a.md").endswith(b"A") and refreshes(fake) == 1


def test_the_renewed_tokens_are_stored_in_the_vault(world):
    now, fake, brain, secret_id = world
    old = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id))
    now[0] += 8 * HOUR - 130
    fake.before("PATCH", lambda: now.__setitem__(0, now[0] + 200), times=1)
    sync_pipeline(brain, "p", trigger="schedule")
    new = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id))
    assert new.access_token != old.access_token and new.refresh_token != old.refresh_token


def test_a_stored_expiry_that_is_later_than_githubs_is_healed_by_the_retry(world):
    now, fake, brain, secret_id = world
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret_id))
    tokens.expires_at += 3 * HOUR                              # the app believes the token lives 3 hours longer than GitHub does
    vault_mod.default_vault().replace(secret_id, tokens.to_json())
    now[0] += 8 * HOUR + 60                                    # GitHub's 8 hours are over; the app does not know
    res = sync_pipeline(brain, "p", trigger="schedule")
    assert res.result == "ok" and refreshes(fake) == 1


def test_a_refused_renewal_says_sign_in_again_and_writes_nothing(world):
    now, fake, brain, secret_id = world
    assert sync_pipeline(brain, "p", trigger="schedule").result == "ok"
    before, head = fake.files(), fake.head()
    fake.revoke_tokens()
    res = sync_pipeline(brain, "p", trigger="schedule")
    assert res.result == "error" and res.category == "sign-in-expired" and "sign in again" in res.message.lower()
    assert "token" not in res.message.lower().replace("sign-in", "")      # never the "create a fine-grained token" advice
    assert fake.files() == before and fake.head() == head and refreshes(fake) == 1


def test_a_second_401_after_a_good_renewal_also_says_sign_in_again_and_renews_only_once(world):
    now, fake, brain, secret_id = world
    fake._failures.append({"method": "GET", "path": "/git/ref/", "status": 401, "message": "Bad credentials", "times": 2})
    res = sync_pipeline(brain, "p", trigger="schedule")
    assert res.result == "error" and res.category == "sign-in-expired" and "sign in again" in res.message.lower()
    assert refreshes(fake) == 1 and fake.read("raw/p/src/a.md") is None


def test_a_token_pipeline_keeps_the_old_text_and_makes_no_sign_in_request(brain, make_source, app_data):
    fake = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    github_api.set_transport(fake)
    try:
        api = github_api.GitHubApi("wrong", allowed_hosts=("api.github.com",))
        with pytest.raises(github_api.TargetAuthError) as exc:
            api.user()
        assert "Create a new fine-grained token" in str(exc.value) and fake.sign_in_log == []
    finally:
        github_api.set_transport(None)


def test_many_threads_hitting_the_same_401_renew_the_token_once():
    calls = []

    def renew(rejected):
        calls.append(rejected)
        time.sleep(0.05)
        return "fresh"

    def transport(method, url, headers, body):
        ok = headers["Authorization"] == "Bearer fresh"
        return (200, {}, b'{"login": "x"}') if ok else (401, {}, b"{}")

    api = github_api.GitHubApi("stale", renew=renew, transport=transport)
    errors = []

    def work():
        try:
            api.user()
        except Exception as exc:                                  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and calls == ["stale"]
