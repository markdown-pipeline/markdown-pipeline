"""How this copy of the app was installed (MD-117): fixed names only, so they can go into ``install_completed`` and pick the update command.

Where the install method comes from, strongest first: ``MDPIPELINE_INSTALL_METHOD`` (the install script, tests), the marker file the install script
leaves in the app data directory, then where the app lives (a ``uv tool`` or ``pipx`` environment, a checkout of the source, else plain pip).
Anything that is not one of the fixed names is ignored: nothing from the person's machine ever becomes a value."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .usage_events import INSTALL_METHOD, OS_NAME
from .vault import data_dir

MARKER = "install-method"


def os_name(platform: str | None = None) -> str:
    platform = platform or sys.platform
    if platform == "darwin":
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    if platform in ("win32", "cygwin"):
        return "windows"
    return "other"


def _is_source_checkout() -> bool:
    here = Path(__file__).resolve()
    return any((parent / ".git").exists() and (parent / "pyproject.toml").exists() for parent in here.parents[:4])


def record_install_method(method: str) -> bool:
    """The install script says how it installed the app. Returns whether it was recorded (only the fixed names are)."""
    if method not in INSTALL_METHOD.values:
        return False
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        (data_dir() / MARKER).write_text(method + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def install_method(*, prefix: str | None = None, source_checkout: bool | None = None) -> str:
    """One of the fixed install methods (``script``, ``uv``, ``pipx``, ``pip``, ``source``, ``other``)."""
    said = os.environ.get("MDPIPELINE_INSTALL_METHOD", "")
    if said in INSTALL_METHOD.values:
        return said
    try:
        marker = (data_dir() / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        marker = ""
    if marker in INSTALL_METHOD.values:
        return marker
    prefix = (prefix if prefix is not None else sys.prefix).replace("\\", "/")
    if "/uv/tools/" in prefix + "/":
        return "uv"
    if "/pipx/venvs/" in prefix + "/":
        return "pipx"
    if source_checkout if source_checkout is not None else _is_source_checkout():
        return "source"
    return "pip"
