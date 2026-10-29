"""MD-198: the gate in front of PyPI. Checks the built wheel and sdist in a folder and exits 1 with the reasons if anything must not be published.

    python scripts/pypi_gate.py dist --version 0.1.10      # (a leading v, as in a release tag, is fine)

Checks: exactly one wheel and one sdist and nothing else; the version is the release's; the metadata says License-Expression Apache-2.0 and
the only address is maintainers@markdownpipeline.com (the owner's old personal address must not appear anywhere); the wheel passes the same
package and secret checks as the GitHub release (scripts/release.py); the sdist holds no secret. The workflow publish-pypi.yml publishes only after this.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
LICENSE = "Apache-2.0"
EMAIL = "maintainers@markdownpipeline.com"
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _release_module():
    spec = importlib.util.spec_from_file_location("release_for_gate", APP / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_for_gate"] = module
    spec.loader.exec_module(module)
    return module


def _headers(text: str):
    return Parser().parsestr(text, headersonly=True)


def metadata_problems(label: str, text: str, version: str) -> list[str]:
    head = _headers(text)
    found: list[str] = []
    if head.get("Name") != "mdpipeline":
        found.append(f"{label}: the name is {head.get('Name')!r}, not 'mdpipeline'")
    if head.get("Version") != version:
        found.append(f"{label}: the version is {head.get('Version')!r} but the release is {version!r}")
    if head.get("License-Expression") != LICENSE:
        found.append(f"{label}: License-Expression is {head.get('License-Expression')!r}, it must be {LICENSE}")
    addresses = set(_ADDRESS.findall("\n".join(f"{k}: {v}" for k, v in head.items())))
    if EMAIL not in addresses:
        found.append(f"{label}: the author address {EMAIL} is missing")
    if addresses - {EMAIL}:
        found.append(f"{label}: other addresses in the metadata ({len(addresses - {EMAIL})}); only {EMAIL} may appear")
    return found


def problems(folder: Path, version: str) -> list[str]:
    """Every reason this folder must not be published (empty = clean)."""
    version = version.removeprefix("v")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.name != ".gitignore")      # uv build leaves a .gitignore beside the build
    wheels = [p for p in files if p.suffix == ".whl"]
    sdists = [p for p in files if p.name.endswith(".tar.gz")]
    found = [f"{p.name}: not a wheel or an sdist; only those two may be in the folder" for p in files if p not in wheels + sdists]
    if len(wheels) != 1:
        found.append(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        found.append(f"expected exactly one sdist, found {len(sdists)}")
    release = _release_module()
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            meta = next((n for n in z.namelist() if n.endswith(".dist-info/METADATA")), None)
            found += metadata_problems("wheel", z.read(meta).decode("utf-8"), version) if meta else ["wheel: no METADATA"]
        found += [f"wheel: {p}" for p in release.wheel_problems(wheel)]
    for sdist in sdists:
        with tarfile.open(sdist) as t:
            members = [m for m in t.getmembers() if m.isfile()]
            info = next((m for m in members if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")), None)
            found += metadata_problems("sdist", t.extractfile(info).read().decode("utf-8"), version) if info else ["sdist: no PKG-INFO"]
            for m in members:
                try:
                    text = t.extractfile(m).read().decode("utf-8")
                except UnicodeDecodeError:
                    continue
                found += [f"sdist {m.name}: contains a {rule}" for rule, rx in release._SECRETS.items() if rx.search(text)]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    found = problems(args.folder, args.version)
    if found:
        print("pypi gate: FAILED, nothing may be published")
        for p in found:
            print("  -", p)
        return 1
    print(f"pypi gate: OK ({args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
