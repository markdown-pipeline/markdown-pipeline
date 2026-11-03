"""MD-205 AC2: no workflow or script fetches a release asset of OUR repo except the two installers (scripts/install.sh, scripts/install.ps1), so CI can't silently inflate the download count."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"scripts/install.sh", "scripts/install.ps1",   # the real clients: Linux/macOS and Windows installers (users, `mdpipeline update`)
           "scripts/local_releases_server.py"}   # a test server that SERVES these paths on 127.0.0.1; it fetches nothing
FETCH = re.compile(r"releases/(download|latest)")
OTHER_REPO = re.compile(r"https://github\.com/(?!markdown-pipeline/)[\w.-]+/[\w.-]+/releases/")   # e.g. the MCP publisher binary


def _files():
    for folder in (".github/workflows", "scripts"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and path.suffix in {".yml", ".yaml", ".sh", ".py", ".ps1"}:
                yield path


def test_only_the_installer_fetches_release_assets_of_our_repo():
    offenders = []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FETCH.search(line) and not OTHER_REPO.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "a workflow/script fetches our release assets (it would count as a download):\n" + "\n".join(offenders)


def test_the_guard_sees_the_third_party_download_it_exempts():
    text = (ROOT / ".github/workflows/publish-registry.yml").read_text(encoding="utf-8")
    assert any(FETCH.search(l) and OTHER_REPO.search(l) for l in text.splitlines())
