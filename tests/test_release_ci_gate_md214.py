"""MD-214: ``scripts/release.py`` will not create the draft release unless the latest ``linux-tests`` and ``windows-tests`` runs are green on exactly the
release commit. ``--allow-red-windows "<reason>"`` overrides the Windows check, is printed, and is recorded in the draft's notes. Nothing here touches the
network: ``gh`` and ``git`` are replaced by a fake runner."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
SHA = "a" * 40
PR_HEAD = "b" * 40
OTHER = "c" * 40
URL = "https://github.com/shvil-ai/md-pipeline-app/actions/runs/"


@pytest.fixture
def rel():
    spec = importlib.util.spec_from_file_location("release_script_md214", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_script_md214"] = module
    spec.loader.exec_module(module)
    module.check_server_json = lambda version, path=None: None        # the repo's real server.json carries the current version; these tests use a made-up one
    return module


def run_row(n: int, sha: str, status: str = "completed", conclusion: str = "success", created: str = "2026-11-10T10:00:00Z") -> dict:
    return {"databaseId": n, "headSha": sha, "status": status, "conclusion": conclusion, "url": URL + str(n), "createdAt": created, "event": "workflow_dispatch"}


class Fake:
    """A runner that answers ``git`` and ``gh`` the way the real ones would, and records what ran."""

    def __init__(self, windows=(), linux=(), parents=(OTHER, PR_HEAD), tag_sha=SHA, version="0.1.21"):
        self.windows, self.linux, self.parents, self.tag_sha, self.version = list(windows), list(linux), parents, tag_sha, version
        self.ran: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.ran.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            return (0, self.tag_sha) if self.tag_sha else (128, "unknown revision")
        if cmd[:2] == ["git", "rev-list"]:
            return 0, " ".join([SHA, *self.parents])
        if cmd[:3] == ["gh", "run", "list"]:
            return 0, json.dumps(self.windows if "windows-tests" in cmd else self.linux)
        if cmd[:3] == ["gh", "release", "create"]:
            return 0, "https://github.com/shvil-ai/md-pipeline-app/releases/tag/v" + self.version
        return 0, ""

    def created(self) -> list[list[str]]:
        return [c for c in self.ran if c[:3] == ["gh", "release", "create"]]


def go(rel, fake, *extra, capsys=None):
    code = rel.run(["--version", "0.1.21", *extra], runner=fake)
    return code, (capsys.readouterr() if capsys else None)


# --- the check itself: green / red / running / other commit -------------------------------------------------------------
def test_green_on_the_release_commit_passes(rel):
    assert rel.ci_problem("windows-tests", [run_row(1, SHA)], {SHA}) is None


def test_a_red_run_is_refused_and_names_the_run(rel):
    msg = rel.ci_problem("windows-tests", [run_row(7, SHA, conclusion="failure")], {SHA})
    assert "windows-tests" in msg and "failure" in msg and URL + "7" in msg


def test_a_run_that_is_still_going_is_refused_and_named(rel):
    msg = rel.ci_problem("windows-tests", [run_row(8, SHA, status="in_progress", conclusion="")], {SHA})
    assert "still running" in msg and URL + "8" in msg


def test_a_green_run_on_another_commit_is_refused_and_says_how_to_start_one(rel):
    msg = rel.ci_problem("windows-tests", [run_row(9, OTHER)], {SHA})
    assert "no windows-tests run on the release commit" in msg and OTHER[:7] in msg and "gh workflow run windows-tests" in msg and URL + "9" in msg


def test_no_run_at_all_is_refused(rel):
    assert "no windows-tests run" in rel.ci_problem("windows-tests", [], {SHA})


def test_only_the_latest_run_on_the_commit_counts(rel):
    red_then_green = [run_row(1, SHA, conclusion="failure", created="2026-11-10T09:00:00Z"), run_row(2, SHA, created="2026-11-10T10:00:00Z")]
    green_then_red = [run_row(1, SHA, created="2026-11-10T09:00:00Z"), run_row(2, SHA, conclusion="failure", created="2026-11-10T10:00:00Z")]
    assert rel.ci_problem("windows-tests", red_then_green, {SHA}) is None
    assert URL + "2" in rel.ci_problem("windows-tests", green_then_red, {SHA})


# --- the command ------------------------------------------------------------------------------------------------------------
def test_check_ci_passes_when_both_are_green_and_creates_nothing(rel, capsys):
    fake = Fake(windows=[run_row(1, SHA)], linux=[run_row(2, SHA)])
    code, out = go(rel, fake, "--check-ci", capsys=capsys)
    assert code == 0 and "green" in out.out and fake.created() == []


def test_a_pull_request_run_counts_for_a_merge_commit_through_its_pr_head(rel, capsys):
    """linux-tests runs on the PR, so its run is stamped with the PR's head commit, not the merge commit the tag points at."""
    fake = Fake(windows=[run_row(1, SHA)], linux=[run_row(2, PR_HEAD)])
    assert go(rel, fake, "--check-ci", capsys=capsys)[0] == 0


def test_windows_must_be_on_the_release_commit_itself_not_on_the_pr_head(rel, capsys):
    fake = Fake(windows=[run_row(1, PR_HEAD)], linux=[run_row(2, SHA)])
    code, out = go(rel, fake, "--check-ci", capsys=capsys)
    assert code == 1 and "windows-tests" in out.err


def test_create_draft_refuses_on_red_windows_and_creates_nothing(rel, capsys):
    fake = Fake(windows=[run_row(3, SHA, conclusion="failure")], linux=[run_row(4, SHA)])
    code, out = go(rel, fake, "--create-draft", capsys=capsys)
    assert code == 1 and URL + "3" in out.err and fake.created() == []


def test_create_draft_refuses_on_red_linux_and_the_override_does_not_cover_it(rel, capsys):
    fake = Fake(windows=[run_row(3, SHA)], linux=[run_row(4, SHA, conclusion="failure")])
    code, out = go(rel, fake, "--create-draft", "--allow-red-windows", "known flake", capsys=capsys)
    assert code == 1 and URL + "4" in out.err and fake.created() == []


def test_create_draft_when_green_creates_a_draft_on_the_private_repo_for_the_tag(rel, capsys):
    fake = Fake(windows=[run_row(1, SHA)], linux=[run_row(2, SHA)])
    code, _ = go(rel, fake, "--create-draft", capsys=capsys)
    (cmd,) = fake.created()
    assert code == 0 and "--draft" in cmd and "v0.1.21" in cmd and "shvil-ai/md-pipeline-app" in cmd and "--verify-tag" in cmd


def test_the_override_is_printed_and_recorded_in_the_drafts_notes(rel, capsys, tmp_path):
    notes = tmp_path / "n.md"
    notes.write_text("Approved notes.\n", encoding="utf-8")
    fake = Fake(windows=[run_row(3, SHA, conclusion="failure")], linux=[run_row(4, SHA)])
    code, out = go(rel, fake, "--create-draft", "--notes-file", str(notes), "--allow-red-windows", "MD-999 flake, owner OK", capsys=capsys)
    (cmd,) = fake.created()
    body = cmd[cmd.index("--notes") + 1]
    assert code == 0 and "MD-999 flake, owner OK" in out.out and URL + "3" in out.out
    assert body.startswith("Approved notes.") and "MD-999 flake, owner OK" in body and URL + "3" in body


def test_an_override_with_an_empty_reason_is_refused(rel, capsys):
    fake = Fake(windows=[run_row(3, SHA, conclusion="failure")], linux=[run_row(4, SHA)])
    code, out = go(rel, fake, "--create-draft", "--allow-red-windows", "  ", capsys=capsys)
    assert code == 1 and "reason" in out.err and fake.created() == []


def test_a_missing_tag_stops_before_asking_github(rel, capsys):
    fake = Fake(tag_sha=None)
    code, out = go(rel, fake, "--check-ci", capsys=capsys)
    assert code == 1 and "tag v0.1.21" in out.err and not [c for c in fake.ran if c[:2] == ["gh", "run"]]


def test_the_publish_path_that_ci_runs_never_asks_github_for_runs(rel, tmp_path):
    """publish-pypi calls ``release.py --publish`` while its own windows-tests run is still going; the gate must not be in that path."""
    import zipfile
    wheel = tmp_path / "mdpipeline-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for name, text in {"mdpipeline/__init__.py": "", "mdpipeline/defaults.json": json.dumps({"github_app": {"client_id": "Iv23li87elyNUpdxbmkx", "slug": "markdown-pipeline"}}),
                           "mdpipeline-0.1.0.dist-info/METADATA": "Name: mdpipeline\n", "mdpipeline/usage_events.py": '"install_completed": Event(...)\n',
                           "mdpipeline/usage.py": '"is_internal": internal_flag()\n'}.items():
            z.writestr(name, text)
    rel.check_server_json = lambda version, path=None: None
    calls: list[list[str]] = []
    rel.run(["--repo", "o/r", "--wheel", str(wheel), "--version", "0.1.0"], runner=lambda cmd: calls.append(cmd) or (0, ""))
    assert not [c for c in calls if c[:3] == ["gh", "run", "list"]]
