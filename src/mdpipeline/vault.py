"""The app vault (MD-79): every secret the app uses is kept here, in the app's own data directory.

* The vault is ONE file (``vault.json``, mode 0600, in a 0700 directory) in the app data directory, never inside a brain,
  a repository or a brain backup.
* With a usable OS keychain (macOS Keychain, Linux Secret Service) the whole content (names, kinds, values) is encrypted with
  AES-256-GCM and the key lives in the keychain.
* MD-163 (owner decision D-P-1020-1): with no keychain the file itself is the protection, the way ``gh``, the AWS CLI and docker do it:
  mode 0600 in a 0700 directory, created that way from the start, and refused (``VaultUnsafe``) when it is group/world-readable or not
  owned by the user. No passphrase, so a scheduled sync recovers after a reboot with nothing to enter.
* Vaults made by v0.1.1-v0.1.3 without a keychain (``kdf: scrypt``, a passphrase) still open with their passphrase once; ``unlock`` then
  moves the secrets to the keychain or the user-only file and the passphrase is never needed again.
* Secrets are write-only: they can be added, replaced and removed. Code asks for a value with ``get`` (to make one
  request); there is no way to show, list or export values, and nothing here ever puts a value in a message.
* A vault that cannot be opened (``VaultLocked``: key missing, passphrase needed or wrong) is never changed or deleted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as _secrets
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import winacl
from .target import TargetError

SERVICE = "mdpipeline-vault"
ACCOUNT = "vault-key"
FILE_NAME = "vault.json"
KINDS = ("github-token", "github-sign-in", "jira-token", "jira-email")
STORES = ("keychain", "file")
SCRYPT = {"n": 2**15, "r": 8, "p": 1}


class VaultError(Exception):
    """The vault cannot do what was asked (message is user-facing and never contains a secret)."""


class VaultUnsafe(VaultError, TargetError):
    """The secrets file (or its directory) is readable by other users or not owned by this user. Nothing was read, written or changed."""

    category = "vault-unsafe"


class VaultLocked(VaultError, TargetError):
    """The vault cannot be opened right now. Nothing was written or deleted; a sync that needs a secret stops here."""

    category = "vault-locked"

    def __init__(self, message: str, needs_passphrase: bool = False) -> None:
        super().__init__(message)
        self.needs_passphrase = needs_passphrase


@dataclass(frozen=True)
class SecretInfo:
    """What the app may show about a secret: never its value."""

    id: str
    name: str
    kind: str
    created: str
    last_used: str | None


# --- the OS keychain -------------------------------------------------------------------------------------------------
class SystemKeychain:
    """The vault key in macOS Keychain (``security``) or the Linux Secret Service (``secret-tool``)."""

    def _tool(self) -> str | None:
        return which("security" if sys.platform == "darwin" else "secret-tool")

    def get(self) -> bytes | None:
        tool = self._tool()
        if tool is None:
            return None
        cmd = [tool, "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"] if sys.platform == "darwin" \
            else [tool, "lookup", "service", SERVICE, "account", ACCOUNT]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            return bytes.fromhex(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
        except ValueError:
            return None

    def set(self, key: bytes) -> bool:
        tool = self._tool()
        if tool is None:
            return False
        try:
            if sys.platform == "darwin":
                # the key (not a person's secret) is briefly visible to this user's own processes on macOS: `security` has no stdin mode
                done = subprocess.run([tool, "add-generic-password", "-U", "-s", SERVICE, "-a", ACCOUNT, "-w", key.hex()],
                                      capture_output=True, text=True, timeout=20)
            else:
                done = subprocess.run([tool, "store", "--label", "Markdown Pipeline vault key", "service", SERVICE, "account", ACCOUNT],
                                      input=key.hex(), capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0 and self.get() == key


_keychain = None          # tests install a fake with set_keychain()


def set_keychain(keychain) -> None:
    global _keychain
    _keychain = keychain


def default_keychain():
    return _keychain or SystemKeychain()


# --- where the vault lives -------------------------------------------------------------------------------------------
def data_dir() -> Path:
    """The app's data directory: ``$MDPIPELINE_DATA_DIR``, else the platform's user data folder."""
    override = os.environ.get("MDPIPELINE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if winacl.is_windows():
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "mdpipeline"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mdpipeline"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "mdpipeline"


def _inside_a_brain(path: Path) -> bool:
    for parent in path.resolve().parents:
        if (parent / ".mdpipeline").exists():
            return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class Vault:
    def __init__(self, path: Path, keychain=None, passphrase: str | None = None) -> None:
        self.path = Path(path)
        self._keychain = keychain if keychain is not None else default_keychain()
        self._passphrase = passphrase

    def __repr__(self) -> str:           # never a value, never a key
        return f"<Vault {self.path}>"

    # -- reading ---------------------------------------------------------------------
    def _check_safe(self) -> None:
        """MD-163 AC2: before reading, refuse a file or directory other users can read, or one that is not ours. Never changes anything."""
        try:
            file_stat = os.stat(self.path)
            dir_stat = os.stat(self.path.parent)
        except FileNotFoundError:
            return
        except OSError:
            return                                                   # unreadable for another reason: the read below says so
        if winacl.is_windows():
            self._check_safe_windows()                               # owner and mode bits mean nothing here: read the access list instead
            return
        me = os.getuid() if hasattr(os, "getuid") else file_stat.st_uid
        if file_stat.st_uid != me or dir_stat.st_uid != me:
            raise VaultUnsafe(f"The app's saved-secrets file {self.path} is not owned by you, so the app will not use it. Nothing was changed. "
                              "Remove it and enter the secrets again (your token or sign-in) in the app.")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise VaultUnsafe(f"The app's saved-secrets file {self.path} can be read by other users, so the app will not use it. "
                              f"Nothing was changed. Fix it with: chmod 600 {self.path}")
        if stat.S_IMODE(dir_stat.st_mode) & 0o077:
            raise VaultUnsafe(f"The folder {self.path.parent} holding the app's saved secrets can be opened by other users, so the app will not use it. "
                              f"Nothing was changed. Fix it with: chmod 700 {self.path.parent}")

    def _check_safe_windows(self) -> None:
        """MD-180 AC2: the same refusals as above, read from the Windows access lists of the file and of its folder."""
        me = winacl.current_user_sid()
        for target, directory in ((self.path, False), (self.path.parent, True)):
            problem = winacl.problem(winacl.read_security(target), me)
            if problem == "owner":
                raise VaultUnsafe(f"The app's saved-secrets file {self.path} is not owned by you, so the app will not use it. Nothing was changed. "
                                  "Remove it and enter the secrets again (your token or sign-in) in the app.")
            if problem == "access":
                fix = winacl.fix_command(target, directory=directory, sid=me)
                what = ("The folder {dir} holding the app's saved secrets can be opened" if directory
                        else "The app's saved-secrets file {file} can be read").format(dir=target, file=target)
                raise VaultUnsafe(f"{what} by other accounts on this computer, so the app will not use it. "
                                  f"Nothing was changed. Fix it in PowerShell with: {fix}")

    def _read_file(self) -> dict | None:
        self._check_safe()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise VaultError(f"The vault file {self.path} cannot be read ({type(exc).__name__}). It was not changed.") from None

    def _key(self, header: dict) -> bytes:
        if header.get("kdf") == "keychain":
            key = self._keychain.get()
            if key is None:
                raise VaultLocked("The vault is locked: its key is not in the OS keychain (a different user or a reset keychain). "
                                  "Nothing was changed. Restore the keychain entry, or remove the vault and enter the secrets again.")
            return key
        if header.get("kdf") == "scrypt":
            if not self._passphrase:
                raise VaultLocked("The vault is locked: enter its passphrase to unlock it. Nothing was changed.", needs_passphrase=True)
            return self._derive(self._passphrase, base64.b64decode(header["salt"]))
        raise VaultError("The vault file is in an unknown format. It was not changed.")

    @staticmethod
    def _derive(passphrase: str, salt: bytes) -> bytes:
        return hashlib.scrypt(passphrase.encode(), salt=salt, maxmem=128 * 1024 * 1024, dklen=32, **SCRYPT)

    def _load(self) -> tuple[dict, dict | None]:
        """(the decrypted payload, the header it came from); an empty payload and None when there is no vault yet."""
        header = self._read_file()
        if header is None:
            return {"secrets": {}}, None
        if header.get("kdf") == "file":
            secrets = header.get("secrets")
            if not isinstance(secrets, dict):
                raise VaultError("The vault file is damaged. It was not changed by the app.")
            return {"secrets": secrets}, header
        key = self._key(header)
        try:
            plain = AESGCM(key).decrypt(base64.b64decode(header["nonce"]), base64.b64decode(header["ciphertext"]), self._aad(header["kdf"]))
            return json.loads(plain), header
        except InvalidTag:
            if header["kdf"] == "scrypt":
                raise VaultLocked("The vault is locked: the passphrase is wrong (or the file is damaged). Nothing was changed.", needs_passphrase=True) from None
            raise VaultError("The vault file is damaged or was changed. It was not changed by the app.") from None
        except (KeyError, ValueError, TypeError):
            raise VaultError("The vault file is damaged. It was not changed by the app.") from None

    @staticmethod
    def _aad(kdf: str) -> bytes:
        return b"mdpipeline-vault-v1|" + kdf.encode()

    # -- writing ---------------------------------------------------------------------
    def _new_header(self) -> tuple[dict, bytes | None]:
        """The header and key of a new vault: the keychain when it works, otherwise the user-only file (no key, no passphrase)."""
        key = _secrets.token_bytes(32)
        if self._keychain.set(key):
            return {"version": 1, "kdf": "keychain"}, key
        return {"version": 1, "kdf": "file"}, None

    def _save(self, payload: dict, header: dict | None) -> None:
        if _inside_a_brain(self.path):
            raise VaultError("The vault must live in the app's data directory, never inside your output folder. Nothing was saved.")
        if header is None:
            header, key = self._new_header()
        elif header.get("kdf") == "file":
            key = None
        else:
            key = self._key(header)
        if header["kdf"] == "file":
            header = {"version": 1, "kdf": "file", "secrets": payload["secrets"]}
        else:
            nonce = _secrets.token_bytes(12)
            header = {**{k: v for k, v in header.items() if k not in ("nonce", "ciphertext", "secrets")}, "nonce": _b64(nonce),
                      "ciphertext": _b64(AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), self._aad(header["kdf"])))}
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)       # a new folder is 0700 from the start (the mode can only be narrowed by umask)
        if winacl.is_windows():
            winacl.restrict_to_current_user(directory, directory=True)   # the user only, nothing inherited; the new file below inherits this
        else:
            os.chmod(directory, 0o700)
        tmp = self.path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)                                   # never reuse a leftover file that may have wider permissions
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(header, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        if winacl.is_windows():
            winacl.restrict_to_current_user(self.path, directory=False)

    # -- the API ---------------------------------------------------------------------
    def add(self, kind: str, name: str, value: str) -> str:
        """Store a new secret and return its id (``vault:<id>`` is how the configuration refers to it)."""
        if kind not in KINDS:
            raise VaultError(f"Unknown kind of secret '{kind}'.")
        if not value:
            raise VaultError("The secret is empty. Nothing was saved.")
        payload, header = self._load()
        secret_id = "v_" + uuid.uuid4().hex[:8]
        payload["secrets"][secret_id] = {"name": name.strip() or kind, "kind": kind, "value": value, "created": _now(), "last_used": None}
        self._save(payload, header)
        return secret_id

    def replace(self, secret_id: str, value: str) -> None:
        payload, header = self._load()
        self._entry(payload, secret_id)["value"] = value
        self._save(payload, header)

    def remove(self, secret_id: str) -> None:
        payload, header = self._load()
        self._entry(payload, secret_id)
        del payload["secrets"][secret_id]
        self._save(payload, header)

    def get(self, secret_id: str) -> str:
        """The value, for the one request that needs it. The last-use date is kept (at most one write per day)."""
        payload, header = self._load()
        entry = self._entry(payload, secret_id)
        today = _now()
        if (entry.get("last_used") or "")[:10] != today[:10]:
            entry["last_used"] = today
            try:
                self._save(payload, header)
            except (OSError, VaultError):
                pass
        return entry["value"]

    def list(self) -> list[SecretInfo]:
        payload, _ = self._load()
        return [SecretInfo(i, e["name"], e["kind"], e["created"], e.get("last_used")) for i, e in sorted(payload["secrets"].items())]

    def has(self, secret_id: str) -> bool:
        return secret_id in self._load()[0]["secrets"]

    def migrate(self) -> None:
        """MD-163 AC5: rewrite a passphrase vault in the current format (the keychain if it works, else the user-only file). Needs the passphrase
        once; the ids, names, kinds and dates are kept. Nothing is changed when the passphrase is wrong."""
        payload, header = self._load()
        if header is not None and header.get("kdf") == "scrypt":
            self._save(payload, None)

    def describe(self) -> dict:
        """Where the vault is and how it is protected (never a secret)."""
        header = self._read_file()
        return {"path": str(self.path), "exists": header is not None, "protection": (header or {}).get("kdf")}

    @staticmethod
    def _entry(payload: dict, secret_id: str) -> dict:
        try:
            return payload["secrets"][secret_id]
        except KeyError:
            raise VaultError(f"There is no secret '{secret_id}' in the vault.") from None


# --- the process-wide vault ------------------------------------------------------------------------------------------
_passphrase: str | None = None


def unlock(passphrase: str) -> None:
    """Open a vault made by v0.1.1-v0.1.3 (protected by a passphrase) and move its secrets to the keychain or the user-only file (MD-163 AC5).

    After this the passphrase is not needed again by any process, and it is never kept or written. With any other vault it does nothing.
    A wrong passphrase raises ``VaultLocked`` and changes nothing."""
    global _passphrase
    vault = Vault(data_dir() / FILE_NAME, default_keychain(), passphrase)
    if (vault._read_file() or {}).get("kdf") == "scrypt":
        vault.migrate()
    _passphrase = None


def reset_session() -> None:
    global _passphrase
    _passphrase = None


def default_vault() -> Vault:
    return Vault(data_dir() / FILE_NAME, default_keychain(), _passphrase)


def readiness() -> dict:
    """Can a secret be saved right now, and where would it go? ``store`` is ``keychain`` or ``file`` (the user-only file; MD-163).

    ``mode`` says what the person must do when not ready: "unlock" a passphrase vault from v0.1.1-v0.1.3 (once; it is then migrated), or
    fix an "unsafe" file (``message`` has the command). A working keychain is probed with a throwaway key only while there is no vault."""
    try:
        header = default_vault()._read_file()
    except VaultUnsafe as exc:
        return {"ready": False, "mode": "unsafe", "store": "file", "message": str(exc)}
    if header is None:
        return {"ready": True, "mode": None, "store": "keychain" if default_keychain().set(_secrets.token_bytes(32)) else "file"}
    if header.get("kdf") == "scrypt":
        return {"ready": False, "mode": "unlock", "store": "file"}
    return {"ready": True, "mode": None, "store": "keychain" if header.get("kdf") == "keychain" else "file"}


# --- which pipelines use a secret ------------------------------------------------------------------------------------
def _refs(pipeline) -> list[str]:
    refs = [(pipeline.destination or {}).get("credential", "")]
    for source in pipeline.sources:
        refs += [source.options.get("email_ref", ""), source.options.get("token_ref", "")]
    return refs


def secret_users(brain, secret_id: str) -> list[str]:
    """Names of the pipelines whose configuration refers to ``vault:<secret_id>``."""
    from .config import load_config

    return [p.name for p in load_config(brain).pipelines if f"vault:{secret_id}" in _refs(p)]


def remove_secret(brain, secret_id: str, vault: Vault | None = None) -> None:
    """Remove a secret from the vault. Refused, with the pipelines named, while one still uses it."""
    users = secret_users(brain, secret_id)
    if users:
        raise VaultError(f"The secret '{secret_id}' is still used by: {', '.join(users)}. Change or remove their target settings first. Nothing was removed.")
    (vault or default_vault()).remove(secret_id)
