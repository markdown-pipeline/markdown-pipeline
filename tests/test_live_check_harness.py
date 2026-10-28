"""MD-73: the live-verification harness (scripts/live_github_check.py) is itself tested, here against the fake: it must say PASS for everything
automatable, so a FAIL on a real scratch repository can only mean a real divergence."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from fake_github import FakeGitHub
from mdpipeline import github_api

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "live_github_check.py"


@pytest.fixture
def live():
    spec = importlib.util.spec_from_file_location("live_github_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_github_check"] = module              # dataclasses with postponed annotations look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake():
    f = FakeGitHub(repo="acme/scratch", branch="main", token="ghp_scratchtoken", files={"README.md": b"scratch"})
    f.protect("protected")
    f._refs["protected"] = f.head()
    yield f
    github_api.set_transport(None)


def run(live, fake, capsys, *extra, token="ghp_scratchtoken", monkeypatch=None):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(token + "\n"))
    code = live.main(["--repo", "acme/scratch", "--scratch-repo-confirmed", "--stdin", "--files", "30", *extra], transport=fake)
    return code, capsys.readouterr().out


def test_it_refuses_without_the_scratch_confirmation(live, fake, capsys):
    assert live.main(["--repo", "acme/scratch", "--stdin"], transport=fake) == 2
    assert "scratch-repo-confirmed" in capsys.readouterr().err


def test_every_automatable_item_passes_against_the_fake_and_the_manual_ones_say_what_to_do(live, fake, capsys, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    code, out = run(live, fake, capsys, "--protected-branch", "protected", "--report", str(report), monkeypatch=monkeypatch)
    results = {r["item"]: r for r in json.loads(report.read_text())}
    assert code == 0, out
    for item in ("1a", "1b", "1b2", "1d", "2a", "2b", "2c", "2d", "3", "3b", "4a", "4b", "5a", "5b", "6a"):
        assert results[item]["status"] == "PASS", (item, results[item])
    assert {i for i, r in results.items() if r["status"] == "MANUAL"} == {"1c", "5c", "5d", "6b"}
    assert "git clone --branch" in results["6b"]["detail"] and sum(len(r["divergences"]) for r in results.values()) == 0


def test_it_only_changes_its_own_work_branch_and_never_the_default_branch(live, fake, capsys, monkeypatch):
    default_head = fake.head("main")
    code, out = run(live, fake, capsys, monkeypatch=monkeypatch)
    assert code == 0 and fake.head("main") == default_head
    work = [b for b in fake._refs if b.startswith("mdp-live-check-")]
    assert len(work) == 1 and fake.forced_updates == 0


def test_cleanup_deletes_the_work_branch(live, fake, capsys, monkeypatch):
    run(live, fake, capsys, "--cleanup", monkeypatch=monkeypatch)
    assert not [b for b in fake._refs if b.startswith("mdp-live-check-")]


def test_the_token_is_never_printed_or_written_to_the_report(live, fake, capsys, monkeypatch, tmp_path):
    report = tmp_path / "r.json"
    code, out = run(live, fake, capsys, "--report", str(report), monkeypatch=monkeypatch)
    assert "ghp_scratchtoken" not in out and "ghp_scratchtoken" not in report.read_text()


def test_a_wrong_token_is_a_fail_not_a_crash(live, fake, capsys, monkeypatch):
    code, out = run(live, fake, capsys, token="ghp_wrong", monkeypatch=monkeypatch)
    assert code == 1 and "FAIL" in out and "ghp_wrong" not in out


def test_the_script_has_no_token_argument():
    text = SCRIPT.read_text()
    assert '"--token"' not in text and "'--token'" not in text


def test_a_live_check_never_registers_its_scratch_brain_in_the_users_scheduler(live, fake, capsys, monkeypatch):
    """MD-169 (comment 10713): same rule as the Jira harness."""
    from mdpipeline import engine
    from mdpipeline.schedulestore import SchedulerStore

    before = engine.register_brain
    run(live, fake, capsys, "--repo", "acme/scratch", "--scratch-repo-confirmed", monkeypatch=monkeypatch)
    assert SchedulerStore().read()["brains"] == []
    assert engine.register_brain is before
