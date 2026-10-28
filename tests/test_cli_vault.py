"""MD-79 / MD-81: the CLI manages vault secrets; values are entered through a hidden prompt or stdin, never as an argument."""
import io
import json

import pytest

from mdpipeline import cli, vault as vault_mod
from vaultperm import WINDOWS, is_user_only, widen
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config, save_config

TOKEN = "ghp_CLIVAULTSECRET0123456789"


def run(capsys, *argv, stdin: str | None = None, monkeypatch=None, brain=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main((["--brain", str(brain.root)] if brain else []) + list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_add_reads_the_value_from_stdin_and_prints_only_the_reference(capsys, monkeypatch):
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "work", "--stdin", stdin=TOKEN + "\n", monkeypatch=monkeypatch)
    assert code == 0, err
    [info] = vault_mod.default_vault().list()
    assert f"vault:{info.id}" in out and TOKEN not in out + err
    assert vault_mod.default_vault().get(info.id) == TOKEN


def test_add_uses_a_hidden_prompt_when_interactive(capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TOKEN)
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "work")
    assert code == 0 and TOKEN not in out + err and len(vault_mod.default_vault().list()) == 1


def test_a_secret_given_as_an_argument_is_refused_and_not_echoed(capsys, brain):
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "w", "--value", TOKEN)
    assert code == 2 and TOKEN not in out + err and vault_mod.default_vault().list() == []
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "w", "--token", TOKEN)
    assert code == 2 and TOKEN not in out + err


def test_without_a_terminal_and_without_stdin_nothing_is_read(capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: False)
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "w")
    assert code == 2 and "--stdin" in err and vault_mod.default_vault().list() == []


def test_list_shows_names_kinds_dates_and_users_but_never_values(capsys, monkeypatch, brain, make_source):
    secret_id = vault_mod.default_vault().add("github-token", "work", TOKEN)
    cfg = load_config(brain)
    cfg.pipelines = [Pipeline.new("gh", [str(make_source("a"))], destination={"type": "github", "repo": "a/b", "branch": "main", "credential": f"vault:{secret_id}"})]
    save_config(brain, cfg)
    code, out, err = run(capsys, "vault", "list", brain=brain)
    assert code == 0 and secret_id in out and "work" in out and "github-token" in out and "gh" in out and TOKEN not in out + err
    code, out, _ = run(capsys, "vault", "list", "--json", brain=brain)
    row = json.loads(out)[0]
    assert row["used_by"] == ["gh"] and TOKEN not in out and "value" not in row


def test_replace_and_remove(capsys, monkeypatch, brain):
    secret_id = vault_mod.default_vault().add("github-token", "work", TOKEN)
    code, out, err = run(capsys, "vault", "replace", secret_id, "--stdin", stdin="ghp_NEW\n", monkeypatch=monkeypatch, brain=brain)
    assert code == 0 and vault_mod.default_vault().get(secret_id) == "ghp_NEW" and "ghp_NEW" not in out + err
    code, out, err = run(capsys, "vault", "remove", secret_id, brain=brain)
    assert code == 0 and vault_mod.default_vault().list() == []


def test_remove_in_use_is_refused_with_the_pipeline_names(capsys, brain, make_source):
    secret_id = vault_mod.default_vault().add("github-token", "work", TOKEN)
    cfg = load_config(brain)
    cfg.pipelines = [Pipeline.new("gh", [str(make_source("a"))], destination={"type": "github", "repo": "a/b", "branch": "main", "credential": f"vault:{secret_id}"})]
    save_config(brain, cfg)
    code, out, err = run(capsys, "vault", "remove", secret_id, brain=brain)
    assert code == 2 and "gh" in err and vault_mod.default_vault().has(secret_id)


def test_there_is_no_show_or_export_command(capsys):
    for verb in ("show", "export", "get", "reveal"):
        with pytest.raises(SystemExit):
            main(["vault", verb, "v_x"])
    capsys.readouterr()


def test_status_says_where_the_vault_is_and_how_it_is_protected(capsys, app_data):
    vault_mod.default_vault().add("github-token", "work", TOKEN)
    code, out, err = run(capsys, "vault", "status")
    assert code == 0 and str(app_data.dir) in out and "keychain" in out and TOKEN not in out


def test_a_passphrase_vault_asks_for_the_passphrase_when_interactive(capsys, app_data, monkeypatch):
    app_data.keychain.available = False
    monkeypatch.setattr(cli, "interactive", lambda: True)
    answers = iter([TOKEN, "my passphrase here", "my passphrase here"])      # the secret, then a new passphrase and its confirmation
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "w")
    assert code == 0, err
    assert vault_mod.Vault(app_data.dir / "vault.json", app_data.keychain, "my passphrase here").list()[0].name == "w"


def test_without_a_keychain_the_cli_stores_a_secret_with_no_passphrase_prompt(capsys, app_data, monkeypatch):
    app_data.keychain.available = False
    monkeypatch.setattr(cli, "interactive", lambda: False)
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "w", "--stdin", stdin=TOKEN + "\n", monkeypatch=monkeypatch)
    assert code == 0 and TOKEN not in out + err and is_user_only(app_data.dir / "vault.json")


def test_an_old_passphrase_vault_without_a_terminal_is_a_clear_error_that_changes_nothing(capsys, app_data, monkeypatch):
    from test_secrets_file import legacy_vault

    app_data.keychain.available = False
    legacy_vault(app_data)
    before = (app_data.dir / "vault.json").read_bytes()
    monkeypatch.setattr(cli, "interactive", lambda: False)
    code, out, err = run(capsys, "vault", "add", "--kind", "github-token", "--name", "w", "--stdin", stdin=TOKEN + "\n", monkeypatch=monkeypatch)
    assert code == 1 and "passphrase" in err.lower() and TOKEN not in out + err and (app_data.dir / "vault.json").read_bytes() == before


def test_vault_status_names_the_unsafe_file_and_the_fix(capsys, app_data, monkeypatch):
    import os

    app_data.keychain.available = False
    run(capsys, "vault", "add", "--kind", "github-token", "--name", "w", "--stdin", stdin=TOKEN + "\n", monkeypatch=monkeypatch)
    widen(app_data.dir / "vault.json")
    code, out, err = run(capsys, "vault", "status", monkeypatch=monkeypatch)
    fix = "PowerShell" if WINDOWS else f"chmod 600 {app_data.dir / 'vault.json'}"
    assert code == 1 and fix in out
