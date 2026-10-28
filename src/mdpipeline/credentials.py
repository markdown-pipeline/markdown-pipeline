"""Credential references (MD-61, MD-65): the configuration stores *where* a token is, never the token.

A reference is ``vault:ID`` (the app vault, MD-79), or, for configurations made before the vault, ``env:NAME`` (an
environment variable) or ``keychain:SERVICE`` (the OS keychain: ``security`` on macOS, ``secret-tool`` on Linux). Resolving returns the secret for one request; it is never written to the configuration, the
state, the log, a backup, the CLI output or an error message.
"""
from __future__ import annotations

import os
import re
from shutil import which
import subprocess
import sys

from . import winacl
from .target import TargetAuthError

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class CredentialError(TargetAuthError):
    """The credential cannot be found. The message names the reference, never a secret."""


def parse_ref(ref: str) -> tuple[str, str]:
    """Return ``(kind, name)`` for ``vault:ID``, ``env:NAME`` or ``keychain:SERVICE``; anything else (e.g. a pasted token) is refused."""
    kind, sep, name = (ref or "").partition(":")
    if not sep or kind not in ("vault", "env", "keychain") or not NAME.match(name):
        raise CredentialError("The credential must be a reference, not the token itself: 'vault:ID' (the app vault; enter the token "
                              "in the app), or an older 'env:VARIABLE_NAME' / 'keychain:SERVICE'.")
    return kind, name


def keychain_command(service: str) -> list[str]:
    if sys.platform == "darwin":
        return ["security", "find-generic-password", "-s", service, "-w"]
    return ["secret-tool", "lookup", "service", service]


def run_keychain(cmd: list[str]) -> tuple[int, str]:
    if which(cmd[0]) is None:
        return 127, ""
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout


def resolve_token(ref: str) -> str:
    kind, name = parse_ref(ref)
    if kind == "vault":
        from .vault import VaultError, VaultLocked, default_vault

        try:
            return default_vault().get(name)
        except VaultLocked:
            raise                                  # a locked vault is its own, retryable situation (category vault-locked)
        except VaultError:
            raise CredentialError(f"The secret '{name}' is not in the vault (it was removed). Enter the token again in the pipeline's target settings.") from None
    if kind == "env":
        value = os.environ.get(name, "").strip()
        if not value:
            raise CredentialError(f"The environment variable {name} is not set or is empty. Set it to the token and try again.")
        return value
    if winacl.is_windows():                        # MD-180 AC4: there is no keychain tool here; say what to do instead (marketing's words, MM-135)
        raise CredentialError("keychain: references don't work on Windows, so this Jira source can't read its token. Nothing was changed or deleted. "
                              "Let the app keep the token instead: mdpipeline pipeline edit <pipeline> --jira-label <label> --replace-jira-token "
                              "(or Edit in the app). Or use an environment variable: --jira-token env:VARIABLE_NAME.")
    code, out = run_keychain(keychain_command(name))
    if code != 0 or not out.strip():
        raise CredentialError(f"No token was found in the OS keychain for the service '{name}'.")
    return out.strip()
