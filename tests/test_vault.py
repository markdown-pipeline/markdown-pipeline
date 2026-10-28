"""MD-79: the app vault: vault file in the app data directory: encrypted with a key in the OS keychain, or (MD-163) a user-only file without one; write-only. See test_secrets_file.py."""
import json
import os
import stat

import pytest

from fake_keychain import FakeKeychain
from vaultperm import is_user_only
from mdpipeline import vault as vault_mod
from mdpipeline.vault import Vault, VaultError, VaultLocked

SECRET = "ghp_SUPERSECRETVALUE123456"


def make(app_data, **kw) -> Vault:
    return Vault(app_data.dir / "vault.json", app_data.keychain, **kw)


def test_the_first_secret_creates_an_encrypted_owner_only_file_and_puts_the_key_in_the_keychain(app_data):
    v = make(app_data)
    secret_id = v.add("github-token", "my token", SECRET)
    path = app_data.dir / "vault.json"
    assert is_user_only(path) and is_user_only(app_data.dir)
    raw = path.read_bytes()
    assert SECRET.encode() not in raw and b"my token" not in raw and b"github-token" not in raw
    assert app_data.keychain.key is not None and len(app_data.keychain.key) == 32 and app_data.keychain.key not in raw
    assert secret_id.startswith("v_")


def test_list_shows_name_kind_and_dates_but_never_the_value(app_data):
    v = make(app_data)
    secret_id = v.add("github-token", "my token", SECRET)
    [info] = v.list()
    assert (info.id, info.name, info.kind) == (secret_id, "my token", "github-token")
    assert info.created and info.last_used is None
    assert SECRET not in json.dumps(info.__dict__) and SECRET not in repr(info) and SECRET not in repr(v)


def test_get_returns_the_value_to_code_and_records_the_last_use(app_data):
    v = make(app_data)
    secret_id = v.add("github-token", "t", SECRET)
    assert v.get(secret_id) == SECRET
    assert v.list()[0].last_used


def test_the_vault_survives_a_restart(app_data):
    secret_id = make(app_data).add("github-token", "t", SECRET)
    assert make(app_data).get(secret_id) == SECRET


def test_replace_changes_the_value_and_keeps_the_id_and_name(app_data):
    v = make(app_data)
    secret_id = v.add("github-token", "t", SECRET)
    v.replace(secret_id, "ghp_NEWVALUE")
    assert v.get(secret_id) == "ghp_NEWVALUE" and v.list()[0].name == "t"
    assert b"ghp_NEWVALUE" not in (app_data.dir / "vault.json").read_bytes()


def test_remove_deletes_the_secret(app_data):
    v = make(app_data)
    secret_id = v.add("github-token", "t", SECRET)
    v.remove(secret_id)
    assert v.list() == []
    with pytest.raises(VaultError):
        v.get(secret_id)


def test_an_unknown_id_is_a_plain_error_that_names_no_value(app_data):
    with pytest.raises(VaultError) as exc:
        make(app_data).get("v_nope")
    assert "v_nope" in str(exc.value)


def test_there_is_no_way_to_export_the_vault(app_data):
    public = {n for n in dir(Vault) if not n.startswith("_")}
    assert public.isdisjoint({"show", "export", "dump", "values", "items", "reveal"})


def test_a_lost_keychain_key_locks_the_vault_and_leaves_the_file_untouched(app_data):
    v = make(app_data)
    secret_id = v.add("github-token", "t", SECRET)
    before = (app_data.dir / "vault.json").read_bytes()
    app_data.keychain.lose()
    with pytest.raises(VaultLocked) as exc:
        make(app_data).get(secret_id)
    assert "keychain" in str(exc.value).lower() and SECRET not in str(exc.value)
    assert (app_data.dir / "vault.json").read_bytes() == before


def test_a_tampered_or_corrupt_file_is_reported_and_never_overwritten(app_data):
    v = make(app_data)
    v.add("github-token", "t", SECRET)
    path = app_data.dir / "vault.json"
    data = bytearray(path.read_bytes())
    data[-8] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(VaultError):
        make(app_data).list()
    with pytest.raises(VaultError):
        make(app_data).add("github-token", "u", "x")
    assert path.read_bytes() == bytes(data)


def test_the_vault_refuses_to_live_inside_a_brain(tmp_path, brain):
    with pytest.raises(VaultError) as exc:
        Vault(brain.root / "vault.json", FakeKeychain()).add("github-token", "t", SECRET)
    assert "output folder" in str(exc.value).lower()
    assert not (brain.root / "vault.json").exists()


def test_the_default_location_is_the_app_data_directory(app_data):
    assert vault_mod.data_dir() == app_data.dir
    vault_mod.default_vault().add("github-token", "t", SECRET)
    assert (app_data.dir / "vault.json").is_file()
