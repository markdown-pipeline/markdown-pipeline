"""MD-116 (FR-S2 AC1): events from the CLI: the first run, a dry run, a credential check, pipeline changes."""
from __future__ import annotations

import builtins
import json

import pytest

from mdpipeline import cli, usage
from mdpipeline.cli import main
from mdpipeline.config import Pipeline
from mdpipeline.usage import UsageStore
from test_jira_cli import SITE, fake  # noqa: F401


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued(*names: str) -> list[dict]:
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def own(e: dict) -> dict:
    return {k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")}


def run(capsys, *argv, brain=None):
    code = main((["--brain", str(brain.root)] if brain else []) + list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_first_cli_run_reports_app_opened_with_first_open_true_and_later_runs_do_not(sharing, capsys):
    run(capsys, "github-app", "show")
    run(capsys, "github-app", "show")
    assert [own(e) for e in queued("app_opened")] == [{"first_open": True}]


def test_the_first_cli_run_starts_the_clock_even_without_consent(capsys):
    run(capsys, "github-app", "show")
    assert UsageStore().ms_since_first_open() is not None and UsageStore().pending == 0


@pytest.mark.parametrize("argv", [["usage", "status"], ["usage", "on"]])
def test_the_usage_command_does_not_report_app_opened(sharing, capsys, argv):
    run(capsys, *argv)
    assert queued("app_opened") == []


def test_answering_yes_on_the_first_interactive_run_reports_the_consent_and_the_open(capsys, monkeypatch, app_data):
    (app_data.dir / "usage.json").unlink()                       # a real first run: nothing is marked as seen
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr(cli, "stderr_visible", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda *a: "s")
    run(capsys, "github-app", "show")
    assert [e["event"] for e in queued()] == ["consent_decided", "install_completed", "app_opened"]       # MD-117: the install funnel opens at the first consent


def test_usage_on_reports_consent_decided_and_usage_off_reports_nothing(capsys):
    run(capsys, "usage", "off")
    assert UsageStore().pending == 0
    run(capsys, "usage", "on")
    assert [own(e) for e in queued("consent_decided")] == [{"choice": "share"}]


def test_a_dry_run_reports_what_the_preview_showed_as_buckets(brain, add_pipeline, make_source, sharing, capsys):
    src = make_source("a", {f"{i}.md": str(i) for i in range(3)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    run(capsys, "sync", "p", "--dry-run", brain=brain)
    assert [own(e) for e in queued("dry_run_viewed")] == [{"items_bucket": "1-10", "would_delete_bucket": "0"}]
    assert queued("sync_completed") == []                        # a dry run is not a sync


def test_a_real_sync_does_not_report_a_dry_run(brain, add_pipeline, make_source, sharing, capsys):
    src = make_source("a", {"1.md": "1"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    run(capsys, "sync", "p", brain=brain)
    assert queued("dry_run_viewed") == [] and len(queued("sync_completed")) == 1


def test_the_jira_connection_test_reports_started_then_completed(brain, fake, sharing, capsys):
    code, _, err = run(capsys, "jira", "test", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", "env:JIRA_EMAIL",
                       "--jira-token", "env:JIRA_TOKEN", brain=brain)
    assert code == 0, err
    assert [e["event"] for e in queued("source_auth_started", "source_auth_completed", "source_auth_failed")] == ["source_auth_started", "source_auth_completed"]


def test_a_failed_jira_connection_test_reports_a_class_and_never_the_message(brain, fake, sharing, capsys, monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "wrong-token")
    code, _, _ = run(capsys, "jira", "test", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", "env:JIRA_EMAIL",
                     "--jira-token", "env:JIRA_TOKEN", brain=brain)
    assert code != 0
    failed = queued("source_auth_failed")
    assert len(failed) == 1 and own(failed[0])["error_class"] == "auth"
    assert "wrong-token" not in json.dumps(queued()) and "acme" not in json.dumps(queued())


def test_adding_a_pipeline_on_the_command_line_reports_it(brain, make_source, sharing, capsys):
    src = make_source("a", {"1.md": "1"})
    code, _, err = run(capsys, "pipeline", "add", "p", "--source", str(src), brain=brain)
    assert code == 0, err
    assert [own(e) for e in queued("pipeline_created")] == [{"n_pipelines": 1}]
    assert [own(e) for e in queued("source_added")] == [{"source_type": "folder", "n_sources": 1}]


def test_a_sync_with_a_revoked_jira_token_reports_sync_failed_auth_and_never_sync_completed(brain, add_pipeline, fake, sharing, capsys, monkeypatch):
    """MD-174 AC6: the existing failure event carries error_class=auth; no new event, nothing from Jira in it."""
    from mdpipeline.config import Source
    from test_jira_cli import SITE as JIRA_SITE

    fake.add_issue("MD", "One")
    add_pipeline(Pipeline("p", [Source(JIRA_SITE, "jira", type="jira", options={"site": JIRA_SITE, "jql": "project = MD",
                                                                                   "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"})]))
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-REVOKED")
    code, _, _ = run(capsys, "sync", "p", brain=brain)
    failed = queued("sync_failed")
    assert code != 0 and len(failed) == 1 and own(failed[0])["error_class"] == "auth" and queued("sync_completed", "guard_triggered") == []
    assert "ATATT" not in json.dumps(queued())
