"""MD-163 (owner decision D-P-1020-1): no OS keychain -> secrets live in a file only the user can read (0600), no passphrase.

Covers AC1-AC2, AC4-AC6 at the vault level; the UI, scheduler and usage pieces have their own tests."""
import base64
import json
import os
import stat
import sys

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from fake_keychain import FakeKeychain
from vaultperm import is_user_only, user_only, widen
from mdpipeline import vault as vault_mod
from mdpipeline.vault import Vault, VaultError, VaultLocked, VaultUnsafe

SECRET = "ghp_SUPERSECRETVALUE123456"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def make(app_data, **kw) -> Vault:
    return Vault(app_data.dir / "vault.json", app_data.keychain, **kw)


@pytest.fixture
def no_keychain(app_data):
    app_data.keychain.available = False
    return app_data


def test_with_a_keychain_nothing_changes_the_vault_is_encrypted_and_the_key_is_in_the_keychain(app_data):
    make(app_data).add("github-token", "t", SECRET)
    header = json.loads((app_data.dir / "vault.json").read_text())
    assert header["kdf"] == "keychain" and SECRET not in json.dumps(header) and app_data.keychain.key is not None
    assert make(app_data).describe()["protection"] == "keychain"


def test_without_a_keychain_a_secret_is_saved_with_no_passphrase(no_keychain):
    secret_id = make(no_keychain).add("github-token", "t", SECRET)
    assert make(no_keychain).get(secret_id) == SECRET


def test_the_secrets_file_is_0600_in_a_0700_directory(no_keychain):
    make(no_keychain).add("github-token", "t", SECRET)
    assert is_user_only(no_keychain.dir / "vault.json")
    assert is_user_only(no_keychain.dir)
    assert make(no_keychain).describe()["protection"] == "file"


def test_the_file_is_created_with_0600_from_the_start_with_no_wider_window(no_keychain, monkeypatch):
    seen: list[int] = []
    real_open = os.open

    def spy(path, flags, mode=0o777, **kw):
        if str(path).startswith(str(no_keychain.dir)) and flags & os.O_CREAT:
            seen.append(mode)
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", spy)
    monkeypatch.setattr(os, "umask", lambda m: 0)            # even a permissive umask must not widen it
    make(no_keychain).add("github-token", "t", SECRET)
    assert seen and all(m == 0o600 for m in seen)


def test_the_directory_is_created_0700_from_the_start(tmp_path):
    fresh = tmp_path / "brand-new" / "mdpipeline"
    old = os.umask(0)
    try:
        Vault(fresh / "vault.json", FakeKeychain(available=False)).add("github-token", "t", SECRET)
    finally:
        os.umask(old)
    assert is_user_only(fresh)


@pytest.mark.windows_unsupported("the mode bits and the `chmod 600` advice are Unix; the Windows refusal and its fix are tested in test_winacl.py and test_winacl_windows.py")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_a_group_or_world_readable_secrets_file_is_refused_with_the_fix(no_keychain, mode):
    secret_id = make(no_keychain).add("github-token", "t", SECRET)
    path = no_keychain.dir / "vault.json"
    os.chmod(path, mode)
    with pytest.raises(VaultUnsafe) as exc:
        make(no_keychain).get(secret_id)
    text = str(exc.value)
    assert f"chmod 600 {path}" in text and SECRET not in text
    assert stat.S_IMODE(path.stat().st_mode) == mode                    # refused, not silently "repaired" or rewritten


@pytest.mark.windows_unsupported("os.getuid does not exist on Windows; the Windows owner check is tested in test_winacl.py")
def test_a_file_owned_by_someone_else_is_refused(no_keychain, monkeypatch):
    secret_id = make(no_keychain).add("github-token", "t", SECRET)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(no_keychain.dir / "vault.json").st_uid + 1)
    with pytest.raises(VaultUnsafe):
        make(no_keychain).get(secret_id)


def test_a_refused_file_is_never_written_to(no_keychain):
    make(no_keychain).add("github-token", "t", SECRET)
    path = no_keychain.dir / "vault.json"
    widen(path)
    before = path.read_bytes()
    with pytest.raises(VaultUnsafe):
        make(no_keychain).add("github-token", "u", "other")
    assert path.read_bytes() == before


def test_an_unsafe_directory_is_refused_too(no_keychain):
    secret_id = make(no_keychain).add("github-token", "t", SECRET)
    widen(no_keychain.dir)
    with pytest.raises(VaultUnsafe) as exc:
        make(no_keychain).get(secret_id)
    fix = "PowerShell" if sys.platform == "win32" else f"chmod 700 {no_keychain.dir}"
    assert fix in str(exc.value)


def test_the_unsafe_error_is_not_a_locked_vault(no_keychain):
    assert not issubclass(VaultUnsafe, VaultLocked) and issubclass(VaultUnsafe, VaultError)
    assert VaultUnsafe.category == "vault-unsafe"


def test_readiness_without_a_keychain_is_ready_and_names_the_store(no_keychain):
    assert vault_mod.readiness() == {"ready": True, "mode": None, "store": "file"}


def test_readiness_with_a_keychain_names_the_store(app_data):
    assert vault_mod.readiness() == {"ready": True, "mode": None, "store": "keychain"}


def test_readiness_of_an_existing_file_vault_after_a_restart_is_ready(no_keychain):
    make(no_keychain).add("github-token", "t", SECRET)
    vault_mod.reset_session()
    assert vault_mod.readiness() == {"ready": True, "mode": None, "store": "file"}


def test_readiness_of_an_unsafe_file_is_not_ready_and_says_why(no_keychain):
    make(no_keychain).add("github-token", "t", SECRET)
    widen(no_keychain.dir / "vault.json")
    ready = vault_mod.readiness()
    assert ready["ready"] is False and ready["mode"] == "unsafe" and ("PowerShell" if sys.platform == "win32" else "chmod 600") in ready["message"]


# --- AC5: v0.1.1-v0.1.3 passphrase vaults ---------------------------------------------------------------------------
def legacy_vault(app_data, passphrase="old pass") -> str:
    """A passphrase vault exactly as v0.1.3 wrote it (scrypt + AES-GCM); returns the id of its one secret."""
    salt, nonce = os.urandom(16), os.urandom(12)
    key = Vault._derive(passphrase, salt)
    payload = {"secrets": {"v_legacy01": {"name": "legacy", "kind": "github-token", "value": SECRET, "created": "2026-10-01T00:00:00Z", "last_used": None}}}
    header = {"version": 1, "kdf": "scrypt", "salt": b64(salt), "nonce": b64(nonce),
              "ciphertext": b64(AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), b"mdpipeline-vault-v1|scrypt"))}
    app_data.dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(app_data.dir / "vault.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(header, handle)
    user_only(app_data.dir / "vault.json", directory=False)
    user_only(app_data.dir, directory=True)
    return "v_legacy01"


def test_a_passphrase_vault_still_asks_for_the_passphrase_before_the_first_unlock(no_keychain):
    legacy_vault(no_keychain)
    assert vault_mod.readiness() == {"ready": False, "mode": "unlock", "store": "file"}
    with pytest.raises(VaultLocked):
        make(no_keychain).list()


def test_unlocking_a_passphrase_vault_moves_the_secrets_to_the_user_only_file(no_keychain):
    secret_id = legacy_vault(no_keychain)
    vault_mod.unlock("old pass")
    raw = json.loads((no_keychain.dir / "vault.json").read_text())
    assert raw["kdf"] == "file" and "salt" not in raw and "ciphertext" not in raw
    assert is_user_only(no_keychain.dir / "vault.json")
    vault_mod.reset_session()                                            # the passphrase is no longer needed: a restart, a new process
    assert make(no_keychain).get(secret_id) == SECRET
    assert make(no_keychain).list()[0].name == "legacy"


def test_migration_keeps_the_ids_names_kinds_and_dates(no_keychain):
    secret_id = legacy_vault(no_keychain)
    before = make(no_keychain, passphrase="old pass").list()
    vault_mod.unlock("old pass")
    assert make(no_keychain).list() == before and make(no_keychain).has(secret_id)


def test_a_wrong_passphrase_migrates_nothing(no_keychain):
    legacy_vault(no_keychain)
    path = no_keychain.dir / "vault.json"
    before = path.read_bytes()
    with pytest.raises(VaultLocked):
        vault_mod.unlock("wrong")
    assert path.read_bytes() == before


def test_a_passphrase_vault_moves_into_the_keychain_when_one_works_now(app_data):
    app_data.keychain.available = False
    secret_id = legacy_vault(app_data)
    app_data.keychain.available = True
    vault_mod.unlock("old pass")
    vault_mod.reset_session()
    assert make(app_data).get(secret_id) == SECRET and make(app_data).describe()["protection"] == "keychain"


def test_the_token_is_not_asked_for_again_after_the_migration(no_keychain):
    secret_id = legacy_vault(no_keychain)
    vault_mod.unlock("old pass")
    vault_mod.reset_session()
    assert vault_mod.default_vault().get(secret_id) == SECRET


# --- AC6: secrets never leak -------------------------------------------------------------------------------------------
def test_no_error_message_contains_a_secret(no_keychain):
    secret_id = make(no_keychain).add("github-token", "t", SECRET)
    widen(no_keychain.dir / "vault.json")
    for call in (lambda: make(no_keychain).get(secret_id), lambda: make(no_keychain).list()):
        with pytest.raises(VaultUnsafe) as exc:
            call()
        assert SECRET not in str(exc.value) and SECRET not in repr(exc.value)


def test_the_repr_of_a_file_vault_has_no_values(no_keychain):
    v = make(no_keychain)
    v.add("github-token", "t", SECRET)
    assert SECRET not in repr(v)
