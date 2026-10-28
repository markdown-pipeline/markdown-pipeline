"""MD-129: scripts/github_app_postcheck.py, the owner's post-check after the GitHub App moves to the org and goes public (no credentials)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "github_app_postcheck.py"
SHIPPED = {"client_id": "Iv23li87elyNUpdxbmkx", "slug": "markdown-pipeline"}


@pytest.fixture
def pc():
    spec = importlib.util.spec_from_file_location("github_app_postcheck", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["github_app_postcheck"] = module
    spec.loader.exec_module(module)
    return module


def api(status=200, owner="markdown-pipeline", client_id=SHIPPED["client_id"]):
    return lambda url: (status, json.dumps({"slug": "markdown-pipeline", "client_id": client_id, "owner": {"login": owner}}))


GOOD_SHOW = f"Client ID: {SHIPPED['client_id']}\nApp slug:  markdown-pipeline\nSource:    shipped default\n"


def by_item(results):
    return {r.item: r for r in results}


def test_everything_matching_passes(pc):
    r = by_item(pc.run_checks(SHIPPED, "markdown-pipeline", api(), lambda: GOOD_SHOW))
    assert {k: v.status for k, v in r.items()} == {"C1": "PASS", "C2": "PASS", "C3": "PASS"}


def test_a_private_app_is_a_fail_with_the_next_step(pc):
    r = by_item(pc.run_checks(SHIPPED, "markdown-pipeline", api(status=404), lambda: GOOD_SHOW))
    assert r["C2"].status == "FAIL" and "Make public" in r["C2"].detail


def test_an_app_still_owned_by_a_person_is_a_fail(pc):
    r = by_item(pc.run_checks(SHIPPED, "markdown-pipeline", api(owner="someone-else"), lambda: GOOD_SHOW))
    assert r["C2"].status == "FAIL" and "someone-else" in r["C2"].detail


def test_a_changed_client_id_is_a_fail_that_says_the_build_needs_the_new_one(pc):
    r = by_item(pc.run_checks(SHIPPED, "markdown-pipeline", api(client_id="Iv23liNEW"), lambda: GOOD_SHOW))
    assert r["C2"].status == "FAIL" and "defaults.json" in r["C2"].detail


def test_a_clean_machine_that_asks_for_setup_is_a_fail(pc):
    r = by_item(pc.run_checks(SHIPPED, "markdown-pipeline", api(), lambda: "Sign in with GitHub is not set up\n"))
    assert r["C3"].status == "FAIL"


def test_no_shipped_client_id_is_a_fail(pc):
    r = by_item(pc.run_checks({"client_id": "", "slug": ""}, "markdown-pipeline", api(), lambda: GOOD_SHOW))
    assert r["C1"].status == "FAIL"


def test_a_clean_machine_command_that_times_out_is_a_c3_fail_with_a_next_step_not_a_traceback(pc):
    import subprocess

    def hangs():
        raise subprocess.TimeoutExpired(["mdpipeline", "github-app", "show"], 60)

    c3 = by_item(pc.run_checks(SHIPPED, "markdown-pipeline", api(), hangs))["C3"]
    assert c3.status == "FAIL" and "60" in c3.detail and "\u2192" in c3.detail and "Traceback" not in c3.detail


def test_the_clean_machine_command_never_inherits_the_callers_terminal(pc, monkeypatch):
    import subprocess

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, GOOD_SHOW, "")

    monkeypatch.setattr(pc.subprocess, "run", fake_run)
    pc._clean_machine_show()
    assert seen["stdin"] is subprocess.DEVNULL
