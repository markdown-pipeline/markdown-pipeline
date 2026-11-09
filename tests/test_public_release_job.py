"""DEV-11 (epic DEV-9): after PyPI publishes, CI publishes the PUBLIC repo's release with the PyPI wheel, from a clean checkout of the tag,
with a least-access GitHub App token; and the MCP Registry is no longer published from this (private) repo (DEV-10)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parent.parent
WORKFLOWS = APP / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "publish-pypi.yml"
needs_workflow = pytest.mark.skipif(not WORKFLOW.exists(), reason="the CI workflows are not synced to the public repo (DEV-11)")
OWNER_ID = "22817822"


def job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["public-release"]


def runs() -> str:
    return "\n".join(s.get("run", "") for s in job()["steps"])


@needs_workflow
def test_it_runs_only_after_the_pypi_publish_succeeded_on_the_self_hosted_runner():
    j = job()
    assert "publish" in j["needs"] and "release-gate" in j["needs"] and "build" in j["needs"]
    assert j["if"] == "needs.publish.result == 'success'"
    assert j["runs-on"] == ["self-hosted", "linux", "x64", "mdp-linux"]
    assert j["environment"] == "public-release" and j["permissions"] == {"contents": "read"}


@needs_workflow
def test_a_rerun_of_this_job_alone_is_still_the_owners_and_on_the_tag():
    first = job()["steps"][0]
    assert first["env"]["OWNER_ID"] == OWNER_ID
    assert '"refs/tags/$TAG"' in first["run"] and '"$ACTOR_ID" = "$OWNER_ID"' in first["run"] and '"$trig_id" = "$OWNER_ID"' in first["run"]


@needs_workflow
def test_it_publishes_from_a_clean_checkout_of_the_tag_and_needs_the_notes_and_a_matching_server_json():
    checkout = next(s for s in job()["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ env.TAG }}", "persist-credentials": False}
    assert 'docs/release-notes-$v.md' in runs() and "server.json" in runs()


@needs_workflow
def test_the_published_wheel_is_the_pypi_wheel_and_the_one_this_run_built():
    text = runs()
    assert "https://pypi.org/pypi/mdpipeline/$v/json" in text and 'u["digests"]["sha256"]' in text
    assert '[ "$got" = "$2" ]' in text and '[ "$got" = "$ours" ]' in text


@needs_workflow
def test_the_token_is_a_contents_only_app_token_for_the_public_repo_only():
    step = next(s for s in job()["steps"] if s.get("uses", "").startswith("actions/create-github-app-token@"))
    assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", step["uses"])            # pinned to a commit
    w = step["with"]
    assert w["owner"] == "markdown-pipeline" and w["repositories"] == "markdown-pipeline" and w["permission-contents"] == "write"
    assert not [k for k in w if k.startswith("permission-") and k != "permission-contents"]


@needs_workflow
def test_release_py_runs_re_run_safe_and_without_the_admin_step():
    assert re.search(r"scripts/release\.py --wheel .* --publish --replace --skip-repo-settings", runs().replace("\\\n", " "))


@needs_workflow
def test_it_fails_unless_the_public_latest_and_tree_are_at_the_tag():
    text = runs()
    assert "select(.isLatest)" in text and '[ "$latest" = "$TAG" ]' in text
    assert "contents/pyproject.toml?ref=main" in text and "contents/server.json?ref=main" in text


@needs_workflow
def test_the_registry_is_not_published_from_this_repo():
    # DEV-10: the Registry's GitHub OIDC grants io.github.<repository owner>/* only: it is published by the public repo's own workflow.
    assert not (WORKFLOWS / "publish-registry.yml").exists()
    assert not [p.name for p in WORKFLOWS.glob("*.yml") if "mcp-publisher" in p.read_text(encoding="utf-8")]
