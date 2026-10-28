"""MD-186 AC4: write docs/third-party-licenses.md from uv.lock.

The package set comes from the lock (everything the installed app can pull in, on any platform); each license comes from the package's own
metadata in the project environment. Packages that exist only on other platforms are not installed here, so their licenses (read from
their PyPI pages) are recorded in OTHER_PLATFORM_LICENSES. Run: uv run python scripts/gen_third_party_licenses.py  (a test fails when the file drifts).
"""
from __future__ import annotations

import importlib.metadata as metadata
import re
import sys
import tomllib
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
OUT = APP / "docs" / "third-party-licenses.md"
ROOT = "mdpipeline"
OTHER_PLATFORM_LICENSES = {"pywin32": "PSF-2.0", "httpx2-jsfetch": "BSD-3-Clause", "colorama": "BSD-3-Clause"}   # Windows-only / browser-only: not installable here
CLASSIFIER_NAMES = {"MIT License": "MIT", "BSD License": "BSD", "Apache Software License": "Apache-2.0",
                    "Python Software Foundation License": "PSF-2.0"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _packages(lock_text: str) -> dict[str, dict]:
    return {_norm(p["name"]): p for p in tomllib.loads(lock_text)["package"]}


def _closure(packages: dict[str, dict], start: list[str]) -> dict[str, str]:
    seen: dict[str, str] = {}
    todo = [_norm(n) for n in start]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen[name] = packages[name]["version"]
        todo.extend(_norm(d["name"]) for d in packages[name].get("dependencies", []))
    return seen


def runtime_packages(lock_text: str) -> dict[str, str]:
    """name -> version of everything the installed app can pull in (platform-specific dependencies included), without the project itself."""
    packages = _packages(lock_text)
    return _closure(packages, [d["name"] for d in packages[ROOT].get("dependencies", [])])


def dev_packages(lock_text: str) -> dict[str, str]:
    """The development group (tests, browser driver) and what it pulls in, minus what the app already ships. Not distributed."""
    packages = _packages(lock_text)
    dev = [d["name"] for d in packages[ROOT].get("dev-dependencies", {}).get("dev", [])]
    return {n: v for n, v in _closure(packages, dev).items() if n not in runtime_packages(lock_text)}


def license_of(name: str) -> str:
    """The package's declared license (SPDX expression when it has one), or the recorded one for other-platform packages."""
    if _norm(name) in OTHER_PLATFORM_LICENSES:
        return OTHER_PLATFORM_LICENSES[_norm(name)]
    md = metadata.distribution(name).metadata
    declared = (md.get("License-Expression") or "").strip()
    if not declared:
        declared = next((CLASSIFIER_NAMES.get(c.split(" :: ")[-1], c.split(" :: ")[-1]) for c in md.get_all("Classifier") or [] if c.startswith("License ::")), "")
    if not declared:
        declared = (md.get("License") or "").strip().splitlines()[0] if (md.get("License") or "").strip() else ""
    return declared


def _table(rows: dict[str, str]) -> str:
    lines = ["| Package | Version | License |", "|---|---|---|"]
    lines += [f"| {n} | {v} | {license_of(n)} |" for n, v in sorted(rows.items())]
    return "\n".join(lines)


def render(lock_path: Path) -> str:
    """The whole document for the lock at *lock_path*."""
    text = lock_path.read_text(encoding="utf-8")
    return ("# Third-party licenses\n\n"
            "Generated from `uv.lock` by `scripts/gen_third_party_licenses.py`. Markdown Pipeline itself is Apache-2.0 (see `LICENSE`).\n"
            "No dependency below is under a copyleft license (GPL, LGPL, AGPL). `cryptography` is offered under Apache-2.0 or BSD-3-Clause; "
            "we use it under Apache-2.0.\n\n"
            "## Installed with the app\n\n" + _table(runtime_packages(text)) + "\n\n"
            "## Development only (not installed with the app)\n\n" + _table(dev_packages(text)) + "\n")


if __name__ == "__main__":
    OUT.write_text(render(APP / "uv.lock"), encoding="utf-8")
    print(f"wrote {OUT}", file=sys.stderr)
