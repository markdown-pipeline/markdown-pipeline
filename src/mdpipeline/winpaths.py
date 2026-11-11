"""Windows path rules (MD-223): one normal spelling for a folder the person typed or the app suggests.

On Windows a path is ``C:\\Users\\me\\x``: backslashes only, an upper-case drive letter, no trailing separator, and never ``~`` or ``%USERPROFILE%``
(marketing review, MD-223). ``ntpath`` does the work, so the rules can be tested on any platform by switching ``is_windows`` on.
On every other platform nothing here is used and paths are exactly as before.
"""
from __future__ import annotations

import ntpath
import os
import sys


def is_windows() -> bool:
    """Whether paths follow Windows rules (tests replace this to exercise the Windows rules elsewhere)."""
    return sys.platform == "win32"


def canonical(path: str) -> str:
    """``~`` expanded from the user's profile, ``/`` -> ``\\``, upper-case drive letter, no trailing separator, made absolute on a real Windows host."""
    full = ntpath.normpath(ntpath.expanduser(path))
    if os.name == "nt":
        full = os.path.abspath(full)
    return full[:1].upper() + full[1:] if full[1:2] == ":" else full


def home() -> str:
    """The user's profile folder as a Windows path."""
    return canonical("~")
