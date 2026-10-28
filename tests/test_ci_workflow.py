"""MD-186 AC6: the Linux test suite runs in CI on the public repo."""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "linux-tests.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_linux_workflow_runs_on_push_pull_request_and_by_hand():
    on = _load()[True]    # YAML 1.1 reads the key `on` as True
    assert set(on) == {"push", "pull_request", "workflow_dispatch"}


def test_the_linux_workflow_installs_from_the_lock_and_runs_the_whole_suite_on_ubuntu_24_04_24_04():
    job = _load()["jobs"]["suite"]
    steps = [s.get("run", "") for s in job["steps"]]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] <= 45
    assert "uv sync --frozen" in steps and any(s.startswith("uv run pytest") for s in steps)
    assert any("playwright install" in s and "chromium" in s for s in steps)


def test_the_linux_workflow_only_reads_the_repository():
    assert _load()["permissions"] == {"contents": "read"}
