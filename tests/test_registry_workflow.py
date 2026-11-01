"""MD-199 AC3: the Registry listing is published by a workflow on the public repo (OIDC, no token, an owner's click), never by hand from a laptop.
`mcp-publisher login github` cannot publish under an organization namespace (Registry docs, authentication page), but OIDC from Actions can."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish-registry.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else ""
DOC = yaml.safe_load(TEXT) if TEXT else {}


def _job() -> dict:
    return DOC["jobs"]["publish"]


def _runs() -> str:
    return "\n".join(s.get("run", "") for s in _job()["steps"])


def test_it_only_runs_when_someone_starts_it_by_hand():
    assert set(DOC[True]) == {"workflow_dispatch"}                    # YAML 1.1 reads the key `on` as True; no push, tag or release trigger


def test_it_waits_for_the_owner_in_a_protected_environment():
    assert _job()["environment"] == "mcp-registry"


def test_only_this_job_gets_an_identity_token_and_nothing_else_is_writable():
    assert _job()["permissions"] == {"id-token": "write", "contents": "read"} and DOC.get("permissions", {}).get("id-token") is None


def test_it_logs_in_with_oidc_and_holds_no_token_or_password():
    runs = _runs()
    assert "./mcp-publisher login github-oidc" in runs
    assert not re.search(r"secrets\.|--token|GITHUB_TOKEN|PAT", TEXT.replace("# ", "#"))


def test_the_publisher_is_pinned_and_its_signature_is_verified_before_it_runs():
    runs = _runs()
    assert re.search(r"MCP_PUBLISHER_VERSION: v\d+\.\d+\.\d+", TEXT) and "releases/latest" not in TEXT
    assert "cosign verify-blob" in runs and runs.index("cosign verify-blob") < runs.index("./mcp-publisher login")


def test_the_entry_is_validated_and_the_pypi_version_must_exist_before_publishing():
    runs = _runs()
    assert "./mcp-publisher validate server.json" in runs
    assert "pypi.org/pypi/mdpipeline/" in runs and runs.index("pypi.org/pypi/mdpipeline/") < runs.index("./mcp-publisher publish")


def test_it_publishes_the_entry_in_the_repo_and_nothing_else():
    runs = _runs()
    assert "./mcp-publisher publish" in runs and runs.count("mcp-publisher publish") == 1


def test_the_runner_is_pinned_like_the_other_workflows():
    assert _job()["runs-on"] == "ubuntu-24.04"
