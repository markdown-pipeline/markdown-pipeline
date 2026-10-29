"""MD-203: glama.json at the root of the source claims the server on glama.ai for the maintainer. It must be tracked (so the public-tree sync, which copies
every tracked file, carries it) and must stay out of the wheel."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
PATH = APP / "glama.json"


def test_glama_json_has_the_schema_and_the_maintainer():
    assert json.loads(PATH.read_text(encoding="utf-8")) == {"$schema": "https://glama.ai/mcp/schemas/server.json", "maintainers": ["avikoren"]}


def test_glama_json_is_a_tracked_file_not_an_ignored_one():
    ignored = subprocess.run(["git", "check-ignore", "-q", "glama.json"], cwd=APP).returncode == 0
    assert not ignored
