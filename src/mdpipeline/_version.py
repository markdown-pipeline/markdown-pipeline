"""The one place the app's version comes from (MD-235): the installed package's metadata, which is pyproject.toml's ``version``.

There is no hand-kept copy anywhere in the source. 0.1.22 shipped saying 0.1.21 because ``__init__.py`` had one.
"""
from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path
from typing import Callable

NAME = "mdpipeline"
UNKNOWN = "0+unknown"           # never a stale release number: an unknown version must look unknown


def resolve(lookup: Callable[[str], str] = importlib.metadata.version, root: Path | None = None) -> str:
    """The installed version; in a source checkout that is not installed, the version in ``pyproject.toml`` next to ``src/``."""
    try:
        return lookup(NAME)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        text = ((root or Path(__file__).resolve().parents[2]) / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        return match.group(1) if match else UNKNOWN
    except OSError:
        return UNKNOWN
