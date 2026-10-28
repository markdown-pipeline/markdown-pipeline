"""MD-180 (W2): the Windows side of the vault's safety check. The judgement is pure (tested here on every OS); reading a real
security descriptor and setting a real ACL run only on Windows (tests/test_winacl_windows.py)."""
import pytest

from mdpipeline import winacl
from mdpipeline.winacl import ADMINISTRATORS, EVERYONE, SYSTEM, Security

ME = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"
USERS = "S-1-5-32-545"
AUTHENTICATED_USERS = "S-1-5-11"


def test_only_me_owning_and_allowed_is_safe():
    assert winacl.problem(Security(owner=ME, allowed=frozenset({ME})), ME) is None


def test_system_and_administrators_may_also_have_access():
    assert winacl.problem(Security(owner=ME, allowed=frozenset({ME, SYSTEM, ADMINISTRATORS})), ME) is None


def test_the_administrators_group_may_own_a_file_an_elevated_process_made():
    assert winacl.problem(Security(owner=ADMINISTRATORS, allowed=frozenset({ME})), ME) is None


def test_another_user_owning_it_is_the_owner_problem():
    assert winacl.problem(Security(owner=OTHER, allowed=frozenset({ME})), ME) == "owner"


@pytest.mark.parametrize("who", [EVERYONE, USERS, AUTHENTICATED_USERS, OTHER])
def test_access_for_anyone_else_is_the_access_problem(who):
    assert winacl.problem(Security(owner=ME, allowed=frozenset({ME, who})), ME) == "access"


def test_the_owner_problem_is_reported_before_the_access_problem():
    assert winacl.problem(Security(owner=OTHER, allowed=frozenset({ME, EVERYONE})), ME) == "owner"


# --- AC1: where the data folder is on Windows -------------------------------------------------------------------------
def test_the_data_folder_on_windows_is_localappdata_mdpipeline(monkeypatch):
    from pathlib import PurePosixPath

    from mdpipeline import vault

    monkeypatch.delenv("MDPIPELINE_DATA_DIR")
    monkeypatch.setenv("LOCALAPPDATA", "/c/Users/me/AppData/Local")
    monkeypatch.setattr(winacl, "is_windows", lambda: True)
    assert PurePosixPath(vault.data_dir()) == PurePosixPath("/c/Users/me/AppData/Local/mdpipeline")


def test_the_override_still_wins_on_windows(monkeypatch, tmp_path):
    from mdpipeline import vault

    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(winacl, "is_windows", lambda: True)
    assert vault.data_dir() == tmp_path / "x"


def test_without_localappdata_windows_falls_back_to_the_home_appdata_local(monkeypatch):
    from mdpipeline import vault

    monkeypatch.delenv("MDPIPELINE_DATA_DIR")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(winacl, "is_windows", lambda: True)
    assert vault.data_dir().parts[-3:] == ("AppData", "Local", "mdpipeline")


# --- the vault on (a fake) Windows -------------------------------------------------------------------------------------
class FakeWindows:
    """Stands in for the Windows calls: a table of who may open what, and a log of the ACLs the app sets."""

    def __init__(self, monkeypatch):
        self.table: dict = {}
        self.restricted: list = []
        monkeypatch.setattr(winacl, "is_windows", lambda: True)
        monkeypatch.setattr(winacl, "current_user_sid", lambda: ME)
        monkeypatch.setattr(winacl, "read_security", lambda path: self.table.get(str(path), Security(ME, frozenset({ME}))))
        monkeypatch.setattr(winacl, "restrict_to_current_user", lambda path, *, directory: self.restricted.append((str(path), directory)))


@pytest.fixture
def fake_windows(monkeypatch):
    return FakeWindows(monkeypatch)


def _vault(app_data):
    from fake_keychain import FakeKeychain
    from mdpipeline.vault import Vault

    return Vault(app_data.dir / "vault.json", FakeKeychain(available=False))


def test_a_new_vault_on_windows_restricts_the_folder_then_the_file_to_the_user(app_data, fake_windows):
    _vault(app_data).add("github-token", "t", "ghp_x")
    assert fake_windows.restricted == [(str(app_data.dir), True), (str(app_data.dir / "vault.json"), False)]


def test_a_file_others_can_open_is_refused_in_the_words_marketing_approved_with_the_fix(app_data, fake_windows):
    from mdpipeline.vault import VaultUnsafe

    secret_id = _vault(app_data).add("github-token", "t", "ghp_x")
    path = app_data.dir / "vault.json"
    fake_windows.table[str(path)] = Security(ME, frozenset({ME, EVERYONE}))
    with pytest.raises(VaultUnsafe) as exc:
        _vault(app_data).get(secret_id)
    fix = winacl.fix_command(path, directory=False, sid=ME)
    assert str(exc.value) == (f"The app's saved-secrets file {path} can be read by other accounts on this computer, so the app will not use it. "
                              f"Nothing was changed. Fix it in PowerShell with: {fix}")


def test_a_folder_others_can_open_is_refused_in_the_words_marketing_approved_with_the_fix(app_data, fake_windows):
    from mdpipeline.vault import VaultUnsafe

    secret_id = _vault(app_data).add("github-token", "t", "ghp_x")
    fake_windows.table[str(app_data.dir)] = Security(ME, frozenset({ME, USERS}))
    with pytest.raises(VaultUnsafe) as exc:
        _vault(app_data).get(secret_id)
    fix = winacl.fix_command(app_data.dir, directory=True, sid=ME)
    assert str(exc.value) == (f"The folder {app_data.dir} holding the app's saved secrets can be opened by other accounts on this computer, "
                              f"so the app will not use it. Nothing was changed. Fix it in PowerShell with: {fix}")


def test_a_file_owned_by_another_account_is_refused_with_the_existing_words(app_data, fake_windows):
    from mdpipeline.vault import VaultUnsafe

    secret_id = _vault(app_data).add("github-token", "t", "ghp_x")
    fake_windows.table[str(app_data.dir / "vault.json")] = Security(OTHER, frozenset({ME}))
    with pytest.raises(VaultUnsafe, match="is not owned by you"):
        _vault(app_data).get(secret_id)


def test_no_windows_refusal_mentions_chmod_acl_or_sid(app_data, fake_windows):
    from mdpipeline.vault import VaultUnsafe

    secret_id = _vault(app_data).add("github-token", "t", "ghp_x")
    for table_path, security in ((app_data.dir / "vault.json", Security(OTHER, frozenset({ME}))),
                                 (app_data.dir / "vault.json", Security(ME, frozenset({ME, EVERYONE}))),
                                 (app_data.dir, Security(ME, frozenset({ME, EVERYONE})))):
        fake_windows.table = {str(table_path): security}
        with pytest.raises(VaultUnsafe) as exc:
            _vault(app_data).get(secret_id)
        before_fix = str(exc.value).split("Fix it in PowerShell with:")[0].replace(str(app_data.dir), "")
        assert not any(word in before_fix.lower() for word in ("chmod", "acl", "sid"))


def test_a_refused_file_on_windows_is_never_written_to(app_data, fake_windows):
    from mdpipeline.vault import VaultUnsafe

    _vault(app_data).add("github-token", "t", "ghp_x")
    path = app_data.dir / "vault.json"
    fake_windows.table[str(path)] = Security(ME, frozenset({ME, EVERYONE}))
    before = path.read_bytes()
    with pytest.raises(VaultUnsafe):
        _vault(app_data).add("github-token", "u", "other")
    assert path.read_bytes() == before


def test_the_fix_is_powershell_text_with_the_users_sid_and_no_secret():
    fix = winacl.fix_command("C:\\Users\\me\\AppData\\Local\\mdpipeline\\vault.json", directory=False, sid=ME)
    assert ME in fix and "icacls" in fix and "/inheritance:r" in fix and "/reset" in fix
    folder = winacl.fix_command("C:\\Users\\me\\AppData\\Local\\mdpipeline", directory=True, sid=ME)
    assert "(OI)(CI)" in folder and "(OI)(CI)" not in fix
