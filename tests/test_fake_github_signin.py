"""MD-76: the fake GitHub also speaks the GitHub App sign-in endpoints (device flow, refresh, installations)."""
import json
from urllib.parse import urlencode

import pytest

from fake_github import FakeGitHub

CLIENT_ID = "Iv1.testclient"


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/a.md": b"w"})
    f.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    return f


def login(fake, path, **form):
    status, _, data = fake("POST", f"https://github.com{path}", {"Accept": "application/json"}, urlencode(form).encode())
    return status, json.loads(data)


def api(fake, method, path, token):
    status, _, data = fake(method, f"https://api.github.com{path}", {"Authorization": f"Bearer {token}"}, None)
    return status, json.loads(data) if data else None


def start(fake):
    return login(fake, "/login/device/code", client_id=CLIENT_ID)[1]


def poll(fake, device_code, **extra):
    return login(fake, "/login/oauth/access_token", client_id=CLIENT_ID, device_code=device_code,
                 grant_type="urn:ietf:params:oauth:grant-type:device_code", **extra)[1]


def signed_in(fake):
    code = start(fake)
    fake.authorize_device()
    return poll(fake, code["device_code"])


def test_device_code_gives_a_user_code_a_link_and_an_interval(fake):
    code = start(fake)
    assert code["user_code"] and code["verification_uri"] == "https://github.com/login/device"
    assert code["expires_in"] == 900 and code["interval"] == 5 and code["device_code"]


def test_an_unknown_client_id_is_refused(fake):
    assert login(fake, "/login/device/code", client_id="nope")[1]["error"] == "incorrect_client_credentials"


def test_device_flow_disabled_in_the_app_settings_is_reported(fake):
    fake.device_flow_enabled = False
    assert login(fake, "/login/device/code", client_id=CLIENT_ID)[1]["error"] == "device_flow_disabled"


def test_polling_before_the_user_approves_is_pending(fake):
    assert poll(fake, start(fake)["device_code"])["error"] == "authorization_pending"


def test_polling_after_approval_gives_expiring_tokens(fake):
    tokens = signed_in(fake)
    assert tokens["access_token"].startswith("ghu_") and tokens["refresh_token"].startswith("ghr_")
    assert tokens["expires_in"] == 28800 and tokens["refresh_token_expires_in"] > 0 and tokens["token_type"] == "bearer"


def test_a_denied_request_reports_access_denied(fake):
    code = start(fake)
    fake.deny_device()
    assert poll(fake, code["device_code"])["error"] == "access_denied"


def test_an_expired_code_reports_expired_token(fake):
    code = start(fake)
    fake.expire_device_code()
    assert poll(fake, code["device_code"])["error"] == "expired_token"


def test_slow_down_can_be_scripted_and_carries_a_longer_interval(fake):
    code = start(fake)
    fake.slow_down(1)
    first = poll(fake, code["device_code"])
    assert first["error"] == "slow_down" and first["interval"] == 10
    assert poll(fake, code["device_code"])["error"] == "authorization_pending"


def test_a_wrong_device_code_is_reported(fake):
    start(fake)
    assert poll(fake, "nope")["error"] == "incorrect_device_code"


def test_the_access_token_authenticates_api_calls_until_it_expires(fake):
    tokens = signed_in(fake)
    assert api(fake, "GET", "/user", tokens["access_token"])[0] == 200
    fake.expire_access_tokens()
    assert api(fake, "GET", "/user", tokens["access_token"])[0] == 401


def test_refresh_without_a_client_secret_works_by_default_and_rotates_the_refresh_token(fake):
    tokens = signed_in(fake)
    fake.expire_access_tokens()
    fresh = login(fake, "/login/oauth/access_token", client_id=CLIENT_ID, grant_type="refresh_token", refresh_token=tokens["refresh_token"])[1]
    assert fresh["access_token"] != tokens["access_token"] and fresh["refresh_token"] != tokens["refresh_token"]
    assert api(fake, "GET", "/user", fresh["access_token"])[0] == 200
    reused = login(fake, "/login/oauth/access_token", client_id=CLIENT_ID, grant_type="refresh_token", refresh_token=tokens["refresh_token"])[1]
    assert reused["error"] == "bad_refresh_token"


def test_refresh_can_be_scripted_to_demand_a_client_secret(fake):
    fake.refresh_requires_secret = True
    tokens = signed_in(fake)
    out = login(fake, "/login/oauth/access_token", client_id=CLIENT_ID, grant_type="refresh_token", refresh_token=tokens["refresh_token"])[1]
    assert out["error"] == "incorrect_client_credentials"


def test_revoking_makes_the_access_token_and_the_refresh_token_unusable(fake):
    tokens = signed_in(fake)
    fake.revoke_tokens()
    assert api(fake, "GET", "/user", tokens["access_token"])[0] == 401
    assert login(fake, "/login/oauth/access_token", client_id=CLIENT_ID, grant_type="refresh_token", refresh_token=tokens["refresh_token"])[1]["error"] == "bad_refresh_token"


def test_installations_list_the_repos_the_app_is_installed_on(fake):
    fake.install_app(["acme/brain"])
    tokens = signed_in(fake)
    status, body = api(fake, "GET", "/user/installations", tokens["access_token"])
    assert status == 200 and body["installations"][0]["permissions"]["contents"] == "write"
    inst = body["installations"][0]["id"]
    repos = api(fake, "GET", f"/user/installations/{inst}/repositories", tokens["access_token"])[1]
    assert [r["full_name"] for r in repos["repositories"]] == ["acme/brain"]


def test_a_repo_without_the_app_is_not_found_for_a_sign_in_token_but_visible_to_a_pat(fake):
    tokens = signed_in(fake)          # the app is not installed anywhere
    assert api(fake, "GET", "/repos/acme/brain", tokens["access_token"])[0] == 404
    assert api(fake, "GET", "/repos/acme/brain", "pat")[0] == 200


def test_a_repo_with_the_app_installed_is_readable_with_the_sign_in_token(fake):
    fake.install_app(["acme/brain"])
    tokens = signed_in(fake)
    assert api(fake, "GET", "/repos/acme/brain", tokens["access_token"])[1]["permissions"]["push"] is True


def test_a_personal_token_cannot_list_installations(fake):
    assert api(fake, "GET", "/user/installations", "pat")[0] == 403


def test_the_sign_in_endpoints_only_answer_on_github_com_login_paths(fake):
    status, _, _ = fake("POST", "https://github.com/other", {}, b"")
    assert status == 404


# MD-169: the browser tests click "Sign in with GitHub" and call authorize_device() at once; the click returns before the server has
# asked the fake for the code, so the fake used to crash with 'NoneType' object does not support item assignment (a flaky test).
def test_authorize_device_waits_for_a_device_code_that_is_still_being_requested(fake):
    import threading

    started = []
    timer = threading.Timer(0.3, lambda: started.append(start(fake)))
    timer.start()
    try:
        fake.authorize_device()
    finally:
        timer.join()
    assert login(fake, "/login/oauth/access_token", client_id=CLIENT_ID, device_code=started[0]["device_code"],
                 grant_type="urn:ietf:params:oauth:grant-type:device_code")[1].get("access_token")


def test_authorize_device_with_no_code_ever_requested_fails_with_a_clear_message(fake):
    with pytest.raises(AssertionError, match="device code"):
        fake.authorize_device(timeout=0.2)
