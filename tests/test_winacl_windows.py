"""MD-180 (W2) AC2, AC3, AC6 on real Windows: a real access list is read and set, and the fix command the refusal prints is run."""
import os
import re
import subprocess
import sys

import pytest

from fake_keychain import FakeKeychain
from mdpipeline import winacl
from mdpipeline.vault import Vault, VaultUnsafe

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="reads and sets real Windows access lists")

SECRET = "ghp_SUPERSECRETVALUE123456"


def icacls(*args: str) -> None:
    subprocess.run(["icacls", *args], check=True, capture_output=True)


def powershell(text: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", text], check=True, capture_output=True)


def make_vault(app_data) -> Vault:
    return Vault(app_data.dir / "vault.json", FakeKeychain(available=False))


def test_the_current_user_sid_is_a_sid():
    assert re.fullmatch(r"S-1-5-21(-\d+)+", winacl.current_user_sid())


def test_restricting_a_file_leaves_access_to_the_user_alone(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("{}")
    winacl.restrict_to_current_user(path, directory=False)
    security = winacl.read_security(path)
    assert security.allowed == {winacl.current_user_sid()}
    assert winacl.problem(security, winacl.current_user_sid()) is None


def test_restricting_a_folder_makes_files_created_in_it_user_only(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    winacl.restrict_to_current_user(folder, directory=True)
    (folder / "new.json").write_text("{}")
    assert winacl.read_security(folder / "new.json").allowed == {winacl.current_user_sid()}


def test_an_entry_for_everyone_is_seen(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("{}")
    winacl.restrict_to_current_user(path, directory=False)
    icacls(str(path), "/grant", f"*{winacl.EVERYONE}:R")
    assert winacl.EVERYONE in winacl.read_security(path).allowed


def test_restricting_again_removes_an_entry_added_by_hand(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("{}")
    icacls(str(path), "/grant", f"*{winacl.EVERYONE}:R")
    winacl.restrict_to_current_user(path, directory=False)
    assert winacl.read_security(path).allowed == {winacl.current_user_sid()}


def test_a_new_vault_has_a_user_only_file_and_folder(app_data):
    make_vault(app_data).add("github-token", "t", SECRET)
    me = winacl.current_user_sid()
    assert winacl.read_security(app_data.dir / "vault.json").allowed == {me}
    assert winacl.read_security(app_data.dir).allowed == {me}


def test_widening_the_file_is_refused_and_the_printed_fix_makes_it_accepted_again(app_data):
    secret_id = make_vault(app_data).add("github-token", "t", SECRET)
    path = app_data.dir / "vault.json"
    icacls(str(path), "/grant", f"*{winacl.EVERYONE}:R")
    with pytest.raises(VaultUnsafe) as exc:
        make_vault(app_data).get(secret_id)
    fix = str(exc.value).split("Fix it in PowerShell with: ")[1]
    powershell(fix)
    assert make_vault(app_data).get(secret_id) == SECRET


def test_widening_the_folder_is_refused_and_the_printed_fix_makes_it_accepted_again(app_data):
    secret_id = make_vault(app_data).add("github-token", "t", SECRET)
    icacls(str(app_data.dir), "/grant", f"*{winacl.EVERYONE}:(OI)(CI)R")
    with pytest.raises(VaultUnsafe) as exc:
        make_vault(app_data).get(secret_id)
    fix = str(exc.value).split("Fix it in PowerShell with: ")[1]
    powershell(fix)
    assert make_vault(app_data).get(secret_id) == SECRET


def test_a_new_process_reopens_the_vault_with_no_user_step(app_data):
    """MD-163 parity (AC3), as far as a test can go: no passphrase, no keychain, a fresh process reads the secret."""
    secret_id = make_vault(app_data).add("github-token", "t", SECRET)
    code = ("from mdpipeline import vault; v = vault.default_vault(); print(v.get(%r))" % secret_id)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "MDPIPELINE_DATA_DIR": str(app_data.dir)})
    assert done.stdout.strip() == SECRET, done.stderr


def test_the_real_default_data_folder_is_localappdata_mdpipeline(monkeypatch):
    """AC1 on a real Windows: no override, the real LOCALAPPDATA."""
    from pathlib import Path

    from mdpipeline import vault

    monkeypatch.delenv("MDPIPELINE_DATA_DIR")
    assert vault.data_dir() == Path(os.environ["LOCALAPPDATA"]) / "mdpipeline"
