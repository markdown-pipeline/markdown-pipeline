"""MD-83 / MD-84 (CLI): pipeline add --auth sign-in, sign in again, sign out, github-app settings."""
import io
import json

import pytest

from fake_github import FakeGitHub
from mdpipeline import cli, github_api, github_signin as gs, vault as vault_mod
from mdpipeline.cli import main
from mdpipeline.config import load_config
from secret_search import files_containing

CLIENT_ID = "Iv1.testclient"


@pytest.fixture
def fake(app_data, monkeypatch):
    f = FakeGitHub(repo="acme/brain", branch="main", token="pat", files={"wiki/w.md": b"W"})
    f.enable_app(client_id=CLIENT_ID, slug="md-pipeline-test")
    f.install_app(["acme/brain"])
    github_api.set_transport(f)
    gs.save_app_settings(CLIENT_ID, "md-pipeline-test")
    approve_after = {"n": 1}

    def sleep(seconds):                                  # the person approves on github.com while the CLI waits
        approve_after["n"] -= 1
        if approve_after["n"] <= 0:
            f.authorize_device()

    monkeypatch.setattr(gs.time, "sleep", sleep)
    f.approve_after = approve_after
    yield f
    github_api.set_transport(None)


def run(capsys, *argv, brain=None, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    try:
        code = main((["--brain", str(brain.root)] if brain else []) + list(argv))
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


def add(capsys, brain, make_source, *extra, repo="acme/brain"):
    return run(capsys, "pipeline", "add", "p", "--source", str(make_source("psrc", {"a.md": "A"})), "--github", repo, "--auth", "sign-in", *extra, brain=brain)


def test_add_with_sign_in_shows_the_code_and_the_link_waits_and_confirms_the_user(fake, brain, make_source, capsys):
    code, out, err = add(capsys, brain, make_source)
    assert code == 0, err
    assert "WDJB-MJHT" in out and "https://github.com/login/device" in out and "Signed in as tester" in out


def test_the_config_holds_only_a_vault_reference_and_the_tokens_are_in_the_vault(fake, brain, make_source, capsys, app_data):
    add(capsys, brain, make_source)
    dest = load_config(brain).get("p").destination
    [secret] = vault_mod.default_vault().list()
    assert dest["auth"] == "sign-in" and dest["credential"] == f"vault:{secret.id}" and secret.kind == "github-sign-in" and dest["branch"] == "main"
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret.id))
    assert tokens.login == "tester"
    for value in (tokens.access_token, tokens.refresh_token):
        assert files_containing(value, brain.root, app_data.dir) == []


def test_sign_in_needs_the_registered_app(fake, brain, make_source, capsys):
    gs.save_app_settings("", "")
    code, out, err = add(capsys, brain, make_source)
    assert code == 2 and "not set up" in err and load_config(brain).pipelines == []


def test_a_denied_sign_in_saves_nothing(fake, brain, make_source, capsys, monkeypatch):
    monkeypatch.setattr(gs.time, "sleep", lambda s: fake.deny_device())
    code, out, err = add(capsys, brain, make_source)
    assert code == 1 and "cancelled" in err.lower() and load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_an_app_not_installed_on_the_repository_shows_the_install_link_and_saves_nothing(fake, brain, make_source, capsys, monkeypatch):
    fake._installed.clear()
    monkeypatch.setattr(cli, "interactive", lambda: False)
    code, out, err = add(capsys, brain, make_source)
    assert code == 2 and "https://github.com/apps/md-pipeline-test/installations/new" in out + err
    assert load_config(brain).pipelines == [] and vault_mod.default_vault().list() == []


def test_when_interactive_it_waits_for_the_installation_and_checks_again(fake, brain, make_source, capsys, monkeypatch):
    fake._installed.clear()
    monkeypatch.setattr(cli, "interactive", lambda: True)

    def press_enter(prompt=""):
        fake.install_app(["acme/brain"])
        return ""

    monkeypatch.setattr("builtins.input", press_enter)
    code, out, err = add(capsys, brain, make_source)
    assert code == 0, err and "install" in out.lower()


def test_a_read_only_installation_is_refused_like_a_read_only_token(fake, brain, make_source, capsys):
    fake.set_permission("read")
    code, out, err = add(capsys, brain, make_source)
    assert code == 2 and "write" in err.lower() and load_config(brain).pipelines == []


def test_a_sync_after_sign_in_commits_with_the_stored_tokens(fake, brain, make_source, capsys):
    add(capsys, brain, make_source)
    code, out, err = run(capsys, "sync", "p", brain=brain)
    assert code == 0, err and any(p.startswith("raw/p/") for p in fake.files())


def test_test_target_reports_the_signed_in_user_as_the_commit_author(fake, brain, make_source, capsys):
    add(capsys, brain, make_source)
    code, out, err = run(capsys, "pipeline", "test-target", "p", brain=brain)
    assert code == 0 and "tester" in out and "author" in out.lower()


def test_sign_in_again_replaces_the_tokens_of_the_pipeline(fake, brain, make_source, capsys):
    add(capsys, brain, make_source)
    [secret] = vault_mod.default_vault().list()
    old = vault_mod.default_vault().get(secret.id)
    fake.revoke_tokens()
    code, out, err = run(capsys, "pipeline", "sign-in", "p", brain=brain)
    assert code == 0, err and "Signed in as tester" in out
    assert vault_mod.default_vault().get(secret.id) != old and len(vault_mod.default_vault().list()) == 1
    assert run(capsys, "sync", "p", brain=brain)[0] == 0


def test_sign_out_removes_the_tokens_from_the_vault_and_says_how_to_revoke(fake, brain, make_source, capsys):
    add(capsys, brain, make_source)
    code, out, err = run(capsys, "pipeline", "sign-out", "p", brain=brain)
    assert code == 0 and vault_mod.default_vault().list() == [] and not load_config(brain).get("p").destination["credential"]
    assert "github.com/settings/applications" in out
    code, out, err = run(capsys, "sync", "p", brain=brain)
    assert code == 1 and "sign in" in (out + err).lower()


def test_sign_in_and_sign_out_only_apply_to_a_github_sign_in_pipeline(fake, brain, make_source, capsys):
    run(capsys, "pipeline", "add", "q", "--source", str(make_source("q", {"a.md": "A"})), brain=brain)
    assert run(capsys, "pipeline", "sign-in", "q", brain=brain)[0] == 2
    assert run(capsys, "pipeline", "sign-out", "q", brain=brain)[0] == 2


def test_a_revoked_sign_in_shows_sign_in_again_in_the_cli_and_changes_nothing(fake, brain, make_source, capsys):
    add(capsys, brain, make_source)
    run(capsys, "sync", "p", brain=brain)
    before = fake.files()
    fake.revoke_tokens()
    secret = vault_mod.default_vault().list()[0]
    tokens = gs.SignInTokens.from_json(vault_mod.default_vault().get(secret.id))
    tokens.expires_at = 1.0
    vault_mod.default_vault().replace(secret.id, tokens.to_json())
    code, out, err = run(capsys, "sync", "p", brain=brain)
    assert code == 1 and "sign in again" in (out + err).lower() and "pipeline sign-in p" in (out + err)
    assert fake.files() == before


def test_github_app_set_and_show(app_data, capsys):
    gs.save_app_settings("", "")
    assert run(capsys, "github-app", "set", "--client-id", "Iv1.abc123", "--slug", "my-app")[0] == 0
    code, out, _ = run(capsys, "github-app", "show")
    assert code == 0 and "Iv1.abc123" in out and "my-app" in out
