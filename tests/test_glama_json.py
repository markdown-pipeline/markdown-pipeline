"""MD-203: glama.json at the root of the source claims the server on glama.ai for the maintainer. It must be tracked (so the public-tree sync, which copies
every tracked file, carries it) and must stay out of the wheel."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
PATH = APP / "glama.json"


def test_glama_json_has_the_schema_and_the_maintainer():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    assert data["$schema"] == "https://glama.ai/mcp/schemas/server.json" and data["maintainers"] == ["avikoren"]


def test_glama_json_matches_the_build_in_the_glama_admin_tab():
    """MD-210: Glama ignores the Dockerfile; it reuses the build spec the owner proved (2026-11-05: success, 6 tools). No credentials; the env schema stays in the Admin tab.
    DEV-12 (2026-11-09): Glama doesn't read this file either; it mirrors the Admin form, which installs the newest mdpipeline (unpinned, Auto-Release on),
    so a release no longer bumps it."""
    data = json.loads(PATH.read_text(encoding="utf-8"))
    assert data["baseImage"] == "debian:trixie-slim"
    assert data["buildSteps"] == ["apt-get update && apt-get install -y --no-install-recommends python3 python3-venv",
                                  "python3 -m venv /opt/mdp",
                                  "/opt/mdp/bin/pip install --no-cache-dir mdpipeline"]
    assert data["cmdArguments"] == ["mcp-proxy", "--", "/opt/mdp/bin/mdpipeline", "--install-method", "registry", "mcp"]
    assert "placeholderArguments" not in data          # an env schema stays in the Admin tab: only keys seen to work are pinned


def test_glama_json_is_a_tracked_file_not_an_ignored_one():
    ignored = subprocess.run(["git", "check-ignore", "-q", "glama.json"], cwd=APP).returncode == 0
    assert not ignored


def test_the_public_tree_plan_includes_glama_json_at_its_root():
    """MD-203 AC2: the plan the release sync makes for the REAL source (not a fake tree) carries glama.json to the root of the public repo."""
    import sys

    sys.path.insert(0, str(APP / "scripts"))
    try:
        import sync_public
    finally:
        sys.path.pop(0)
    assert sync_public.wanted(APP).get("glama.json") == "glama.json"
