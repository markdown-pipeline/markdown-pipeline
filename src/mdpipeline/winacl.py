"""MD-180 (W2): who can open the app's saved-secrets file and its folder, on Windows.

POSIX owner and mode bits mean nothing on Windows, so the vault's safety check reads the security descriptor instead. The judgement
(``problem``) is pure and runs on every OS; the Windows calls come after it and import nothing at module level that only Windows has."""
from __future__ import annotations

import sys
from dataclasses import dataclass

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
EVERYONE = "S-1-1-0"


def is_windows() -> bool:
    """The one place that asks; tests replace it to run the Windows branches on any OS."""
    return sys.platform == "win32"


@dataclass(frozen=True)
class Security:
    """The owner's SID and the SIDs that have an *allow* entry on a file or folder."""
    owner: str
    allowed: frozenset[str]


def problem(security: Security, me: str) -> str | None:
    """``"owner"``, ``"access"`` or ``None``. The owner problem comes first.

    SYSTEM and Administrators may own or reach the file: an elevated process creates files owned by Administrators, and an
    administrator can take any file anyway, so refusing them would only break backups and elevated runs."""
    trusted = {me, SYSTEM, ADMINISTRATORS}
    if security.owner not in trusted:
        return "owner"
    if security.allowed - trusted:
        return "access"
    return None


def fix_command(path, *, directory: bool, sid: str) -> str:
    """The PowerShell text that gives ``path`` back the access list the app itself creates: the user only, nothing inherited.

    ``/reset`` first, so an entry added by hand for another account does not survive; ``/inheritance:r`` then drops what the
    parent folder passes down; the grant is by SID, so it works for any account name and any display language."""
    grant = f'"*{sid}:(OI)(CI)F"' if directory else f'"*{sid}:F"'
    quoted = f'"{path}"'
    return f"icacls {quoted} /reset; icacls {quoted} /inheritance:r; icacls {quoted} /grant:r {grant}"


# --- the Windows calls (exercised by tests/test_winacl_windows.py on the Windows runner) --------------------------------
_CREATE_NO_WINDOW = 0x08000000          # no console flashes when the background app sets an access list
_ACCESS_ALLOWED_ACE_TYPE = 0
_ACCESS_ALLOWED_CALLBACK_ACE_TYPE = 9
_INHERIT_ONLY_ACE = 0x08                # applies to children only, not to the object itself


def _dlls():
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.GetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    return ctypes, advapi32, kernel32


def _sid_string(sid_pointer: int) -> str:
    ctypes, advapi32, kernel32 = _dlls()
    text = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def current_user_sid() -> str:
    """The SID of the account this process runs as, e.g. ``S-1-5-21-...-1001``."""
    ctypes, advapi32, kernel32 = _dlls()
    from ctypes import wintypes

    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):      # TOKEN_QUERY
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))                            # TokenUser: ask for the size
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _sid_string(ctypes.c_void_p.from_buffer(buffer).value)                                  # TOKEN_USER starts with the SID pointer
    finally:
        kernel32.CloseHandle(token)


def read_security(path) -> Security:
    """The owner and the *allow* entries of ``path`` as they apply to the object itself (inherit-only entries are skipped).

    No access list at all means everyone has full access, so it reads as an entry for Everyone."""
    ctypes, advapi32, kernel32 = _dlls()
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    code = advapi32.GetNamedSecurityInfoW(str(path), 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))   # file object; owner + DACL
    if code:
        raise OSError(code, f"cannot read who can open {path}")
    try:
        allowed: set[str] = set()
        if not dacl.value:
            allowed.add(EVERYONE)
        else:
            for index in range(ctypes.c_ushort.from_address(dacl.value + 4).value):                       # ACL.AceCount
                ace = ctypes.c_void_p()
                if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                kind, flags = ctypes.c_ubyte.from_address(ace.value).value, ctypes.c_ubyte.from_address(ace.value + 1).value
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                if kind in (_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_ALLOWED_CALLBACK_ACE_TYPE) and not flags & _INHERIT_ONLY_ACE and mask:
                    allowed.add(_sid_string(ace.value + 8))
        return Security(owner=_sid_string(owner.value), allowed=frozenset(allowed))
    finally:
        kernel32.LocalFree(descriptor)


def restrict_to_current_user(path, *, directory: bool) -> None:
    """Give ``path`` an access list with the current user only and nothing inherited. A folder passes that on to what is created in it."""
    import subprocess

    sid = current_user_sid()
    grant = f"*{sid}:(OI)(CI)F" if directory else f"*{sid}:F"
    for args in (["/reset"], ["/inheritance:r"], ["/grant:r", grant]):
        done = subprocess.run(["icacls", str(path), *args], capture_output=True, creationflags=_CREATE_NO_WINDOW)
        if done.returncode:
            raise OSError(f"cannot restrict who can open {path} (icacls {args[0]} exited {done.returncode})")
