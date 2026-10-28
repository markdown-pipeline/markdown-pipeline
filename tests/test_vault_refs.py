"""MD-79: the configuration refers to secrets as vault:<id>; locked vault stops only the syncs that need it; in-use secrets cannot be removed."""
import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api, vault as vault_mod
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.credentials import CredentialError, parse_ref, resolve_token
from mdpipeline.engine import sync_all
from mdpipeline.vault import VaultError, secret_users, remove_secret

TOKEN = "ghp_VAULTEDSECRET0123456789"


def test_a_vault_reference_is_a_valid_reference():
    assert parse_ref("vault:v_0a1b2c3d") == ("vault", "v_0a1b2c3d")


def test_a_pasted_token_is_still_refused_as_a_reference():
    with pytest.raises(CredentialError):
        parse_ref(TOKEN)


def test_resolving_a_vault_reference_returns_the_value():
    secret_id = vault_mod.default_vault().add("github-token", "t", TOKEN)
    assert resolve_token(f"vault:{secret_id}") == TOKEN


def test_a_reference_to_a_missing_secret_says_so_without_a_value():
    with pytest.raises(CredentialError) as exc:
        resolve_token("vault:v_missing0")
    assert "vault" in str(exc.value).lower()


def test_a_locked_vault_is_reported_as_a_target_error_with_its_own_category(app_data):
    secret_id = vault_mod.default_vault().add("github-token", "t", TOKEN)
    app_data.keychain.lose()
    with pytest.raises(vault_mod.VaultLocked) as exc:
        resolve_token(f"vault:{secret_id}")
    assert exc.value.category == "vault-locked"


def test_secret_users_lists_the_pipelines_that_use_a_secret(brain, make_source):
    cfg = load_config(brain)
    cfg.pipelines = [
        Pipeline.new("gh", [str(make_source("a"))], destination={"type": "github", "repo": "a/b", "branch": "main", "credential": "vault:v_aaaa1111"}),
        Pipeline.new("jira", [Source("https://x.atlassian.net", "jira", type="jira", options={"email_ref": "vault:v_bbbb2222", "token_ref": "vault:v_aaaa1111"})]),
        Pipeline.new("plain", [str(make_source("b"))]),
    ]
    save_config(brain, cfg)
    assert secret_users(brain, "v_aaaa1111") == ["gh", "jira"] and secret_users(brain, "v_bbbb2222") == ["jira"] and secret_users(brain, "v_cccc3333") == []


def test_removing_a_secret_in_use_is_refused_and_lists_the_pipelines(brain, make_source):
    secret_id = vault_mod.default_vault().add("github-token", "t", TOKEN)
    cfg = load_config(brain)
    cfg.pipelines = [Pipeline.new("gh", [str(make_source("a"))], destination={"type": "github", "repo": "a/b", "branch": "main", "credential": f"vault:{secret_id}"})]
    save_config(brain, cfg)
    with pytest.raises(VaultError) as exc:
        remove_secret(brain, secret_id)
    assert "gh" in str(exc.value) and vault_mod.default_vault().has(secret_id)


def test_an_unused_secret_can_be_removed(brain):
    secret_id = vault_mod.default_vault().add("github-token", "t", TOKEN)
    remove_secret(brain, secret_id)
    assert not vault_mod.default_vault().has(secret_id)


def test_a_locked_vault_stops_the_github_pipeline_writes_nothing_and_lets_the_local_one_sync(gh, app_data, make_source):
    secret_id = vault_mod.default_vault().add("github-token", "t", gh.token)
    gh.pipeline("remote", {"r.md": "R"}, destination={**gh.destination, "credential": f"vault:{secret_id}"})
    cfg = load_config(gh.brain)
    cfg.pipelines.append(Pipeline.new("local", [str(make_source("loc", {"l.md": "L"}))]))
    save_config(gh.brain, cfg)
    app_data.keychain.lose()
    gh.fake.requests.clear()
    results = {r.pipeline: r for r in sync_all(gh.brain, trigger="cli")}
    assert results["remote"].result == "error" and results["remote"].category == "vault-locked"
    assert "locked" in results["remote"].message.lower() and gh.token not in results["remote"].message
    assert [r for r in gh.fake.requests if r[0] != "GET"] == [] and gh.fake.read("raw/remote/remote/r.md") is None
    assert results["local"].result == "ok"


def test_a_vault_reference_authenticates_a_github_sync(gh):
    secret_id = vault_mod.default_vault().add("github-token", "t", gh.token)
    gh.pipeline("p", {"a.md": "A"}, destination={**gh.destination, "credential": f"vault:{secret_id}"})
    res = sync_all(gh.brain, trigger="cli")[0]
    assert res.result == "ok" and gh.fake.read("raw/p/p/a.md").endswith(b"A")


def test_a_locked_vault_shows_up_in_needs_you_with_a_next_step(gh, app_data):
    from mdpipeline.engine import sync_pipeline
    from mdpipeline.uiactivity import activity_view

    secret_id = vault_mod.default_vault().add("github-token", "t", gh.token)
    gh.pipeline("remote", {"r.md": "R"}, destination={**gh.destination, "credential": f"vault:{secret_id}"})
    app_data.keychain.lose()
    sync_pipeline(gh.brain, "remote", trigger="ui")
    [card] = activity_view(gh.brain)["needs_you"]
    assert card["category"] == "vault-locked" and card["pipeline"] == "remote" and "locked" in card["title"].lower()
    assert "keychain" in card["next_step"].lower() or "passphrase" in card["next_step"].lower()


def test_after_a_vault_backed_sync_the_secret_is_nowhere_but_encrypted_in_the_vault(gh, app_data, capsys):
    from secret_search import files_containing, repo_containing
    from mdpipeline.cli import main

    secret_id = vault_mod.default_vault().add("github-token", "t", gh.token)
    gh.pipeline("p", {"a.md": "A"}, destination={**gh.destination, "credential": f"vault:{secret_id}"})
    capsys.readouterr()
    assert sync_all(gh.brain, trigger="cli")[0].result == "ok"
    for argv in (["pipeline", "list"], ["status"], ["log"], ["vault", "list"], ["vault", "status"], ["sync", "p", "--dry-run", "--json"]):
        main(["--brain", str(gh.brain.root), *argv])
    out = capsys.readouterr()
    assert gh.token not in out.out + out.err
    assert files_containing(gh.token, gh.brain.root, gh.tmp / "sources", app_data.dir) == []        # config, state, log, backups, runtime, vault file
    assert repo_containing(gh.token, gh.fake) == []
