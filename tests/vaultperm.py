"""MD-180 (W2): who-can-open-the-vault in a test, on any OS. Unix uses mode bits (chmod); Windows uses access lists, because there the
mode bits mean nothing. The product's own Windows code is tested in test_winacl.py / test_winacl_windows.py; these helpers set up and read."""
import os
import subprocess
import sys
import types

WINDOWS = sys.platform == "win32"


def widen(path) -> None:
    """Let every account read ``path``: chmod 644/755 on Unix, an entry for Everyone on Windows."""
    if WINDOWS:
        subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:" + ("(OI)(CI)R" if os.path.isdir(path) else "R")], check=True, capture_output=True)
    else:
        os.chmod(path, 0o755 if os.path.isdir(path) else 0o644)


def user_only(path, *, directory: bool) -> None:
    """What the app itself does to its vault file or folder: the user only. For files a test writes by hand, as an older version would have."""
    if WINDOWS:
        from mdpipeline import winacl

        winacl.restrict_to_current_user(path, directory=directory)
    else:
        os.chmod(path, 0o700 if directory else 0o600)


def is_user_only(path) -> bool:
    """True when only the owner can open ``path`` (0600/0700 on Unix; the user's own entry alone on Windows)."""
    if WINDOWS:
        from mdpipeline import winacl

        return winacl.read_security(path).allowed == {winacl.current_user_sid()}
    return os.stat(path).st_mode & 0o077 == 0


def pretend_a_keychain_exists(monkeypatch) -> None:
    """Windows refuses `keychain:` references outright (MD-180 AC4, tested in test_credentials.py). A test about what happens to an old
    `keychain:` reference (import, sync) fakes `run_keychain` and needs the reference to reach it, so credentials sees a non-Windows OS."""
    from mdpipeline import credentials

    monkeypatch.setattr(credentials, "winacl", types.SimpleNamespace(is_windows=lambda: False))
