"""MD-31: the README quickstart works exactly as written (no undocumented step)."""
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mdpipeline.naming import SEPARATOR

README = Path(__file__).parent.parent / "docs" / "reference.md"


def quickstart_script() -> str:
    match = re.search(r"```bash quickstart\n(.*?)```", README.read_text(), re.S)
    assert match, "README must contain a ```bash quickstart block"
    return match.group(1)


def test_readme_covers_the_required_topics():
    text = README.read_text().lower()
    for topic in ("install", "init", "pipeline add", "--dry-run", "sync proj", "claude mcp add", "codex",
                  "guard", "conflict", "two", "labelled folders"):
        assert topic in text, topic


def test_quickstart_block_runs_verbatim_in_a_clean_home(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "mdpipeline"
    shim.write_text(f"#!/bin/sh\nexec {sys.executable} -m mdpipeline \"$@\"\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    # MD-169: this env is built by hand, so the conftest kill switches do not reach it: without them the first sync starts a real,
    # detached `mdpipeline background` that outlives the test (11 leaked processes on 2026-10-20), and the usage key could be live.
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", "MDPIPELINE_NO_BACKGROUND_START": "1",
           "MDPIPELINE_POSTHOG_KEY": "", "MDPIPELINE_INTERNAL": "1"}
    result = subprocess.run(["bash", "-e", "-c", quickstart_script()], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    brain = home / "demo/brain"
    # new pipelines default to the date-name policy: <first-seen date><sep><slug>.md
    today = datetime.now(timezone.utc).date().isoformat()
    assert (brain / "raw" / f"{today}{SEPARATOR}pricing.md").read_text().startswith("---\nprovenance:")
    api = (brain / "raw" / f"{today}{SEPARATOR}api.md").read_text()
    assert "title: API spec" in api and "pipeline: proj" in api
    assert "claude mcp add" in result.stdout and str(brain) in result.stdout
    assert "dry run" in result.stdout.lower() and "created 2" in result.stdout
