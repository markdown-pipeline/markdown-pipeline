"""MD-186 AC6: the Linux test suite runs in CI on the public repo."""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "linux-tests.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_linux_workflow_runs_on_pull_request_and_by_hand_only():          # owner 2026-11-06: no second run on the merge to main (private repo, Actions minutes)
    on = _load()[True]    # YAML 1.1 reads the key `on` as True
    assert set(on) == {"pull_request", "workflow_dispatch"}


def test_the_linux_workflow_installs_from_the_lock_and_runs_the_whole_suite_on_the_self_hosted_runner():
    job = _load()["jobs"]["suite"]
    steps = [s.get("run", "") for s in job["steps"]]
    assert job["runs-on"] == ["self-hosted", "linux", "x64", "mdp-linux"] and job["timeout-minutes"] <= 45
    assert "uv sync --frozen" in steps and any(s.startswith("uv run pytest") for s in steps)
    assert any("playwright install" in s and "chromium" in s for s in steps)


def test_the_linux_workflow_only_reads_the_repository():
    assert _load()["permissions"] == {"contents": "read"}
