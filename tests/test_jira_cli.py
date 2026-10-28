"""MD-46: add a Jira source to a pipeline from the CLI (site, credential references, JQL) with validation and test connection."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.cli import main
from mdpipeline.config import load_config

EMAIL, TOKEN = "me@example.com", "ATATT-SECRET-TOKEN"
SITE = "https://acme.atlassian.net"


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    f.add_issue("MP", "other")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def run(capsys, brain, *argv):
    code = main(["--brain", str(brain.root), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


JIRA = ["--jira-label", "jira", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN"]


def test_a_pipeline_can_be_added_with_only_a_jira_source(brain, fake, capsys):
    code, out, err = run(capsys, brain, "pipeline", "add", "p", *JIRA)
    assert code == 0, err
    src = load_config(brain).get("p").sources[0]
    assert src.type == "jira" and src.label == "jira" and src.options["site"] == SITE and src.options["jql"] == "project = MD"
    text = brain.config_path.read_text()
    assert "env:JIRA_TOKEN" in text and TOKEN not in text and EMAIL not in text
    assert "3 matching" in out or "3 " in out


def test_folder_and_jira_sources_can_be_mixed_and_labels_stay_unique(brain, fake, capsys, make_source):
    folder = make_source("docs", {"a.md": "A"})
    code, _, err = run(capsys, brain, "pipeline", "add", "p", "--source", str(folder), *JIRA)
    assert code == 0, err
    assert [s.type for s in load_config(brain).get("p").sources] == ["folder", "jira"]
    code, _, err = run(capsys, brain, "pipeline", "add", "q", "--source", f"jira={folder}", *JIRA)
    assert code == 2 and "more than once" in err and load_config(brain).get("q") is None


def test_a_jira_flag_without_the_others_is_a_usage_error(brain, fake, capsys):
    code, _, err = run(capsys, brain, "pipeline", "add", "p", "--jira-site", SITE)
    assert code == 2 and "--jira-jql" in err and load_config(brain).get("p") is None


def test_at_least_one_source_is_required(brain, fake, capsys):
    code, _, err = run(capsys, brain, "pipeline", "add", "p")
    assert code == 2 and "source" in err.lower()


@pytest.mark.parametrize("override,needle", [
    (("--jira-site", "https://evil.example.com"), "atlassian.net"),
    (("--jira-site", "http://acme.atlassian.net"), "atlassian.net"),
    (("--jira-jql", "project = = ="), "Error in the JQL Query"),
    (("--jira-jql", ""), "--jira-jql"),
    (("--jira-token", "ATATT-LITERAL-TOKEN-VALUE"), "reference"),
    (("--jira-token", "env:NOT_SET_ANYWHERE"), "NOT_SET_ANYWHERE"),
])
def test_validation_failures_save_nothing_with_plain_messages(brain, fake, capsys, override, needle):
    argv = list(JIRA)
    argv[argv.index(override[0]) + 1] = override[1]
    code, out, err = run(capsys, brain, "pipeline", "add", "p", *argv)
    assert code == 2 and needle in err and load_config(brain).get("p") is None
    assert "ATATT-LITERAL" not in out + err


def test_a_rejected_token_is_reported_and_nothing_is_saved(brain, fake, capsys, monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    code, out, err = run(capsys, brain, "pipeline", "add", "p", *JIRA)
    assert code == 2 and "rejected the email or API token" in err and "ATATT-WRONG" not in out + err and load_config(brain).get("p") is None


def test_no_permission_and_site_not_found_and_outage_are_distinct_messages(brain, fake, capsys):
    fake.fail("GET", "/myself", status=403, times=1)
    assert "not allowed" in run(capsys, brain, "pipeline", "add", "p", *JIRA)[2]
    fake.fail("GET", "/myself", status=404, times=1)
    assert "not found" in run(capsys, brain, "pipeline", "add", "p", *JIRA)[2]
    fake.fail("GET", "/myself", status=503, times=1)
    assert "not available" in run(capsys, brain, "pipeline", "add", "p", *JIRA)[2]
    assert load_config(brain).get("p") is None


def test_jira_test_reports_the_user_and_the_match_count_and_writes_nothing(brain, fake, capsys):
    code, out, _ = run(capsys, brain, "jira", "test", "--jira-site", SITE, "--jira-jql", "project in (MD, MP)", "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN")
    assert code == 0 and "Mia Owner" in out and "4" in out and TOKEN not in out
    assert not [r for r in fake.requests if r[0] != "GET" and not r[1].endswith("approximate-count")]
    assert load_config(brain).pipelines == []


def test_jira_test_for_a_saved_source(brain, fake, capsys):
    run(capsys, brain, "pipeline", "add", "p", *JIRA)
    code, out, _ = run(capsys, brain, "jira", "test", "--pipeline", "p", "--source", "jira")
    assert code == 0 and "Mia Owner" in out and "3" in out


def test_jira_test_failure_exits_nonzero_with_the_reason(brain, fake, capsys, monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    code, out, err = run(capsys, brain, "jira", "test", "--jira-site", SITE, "--jira-jql", "project = MD", "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN")
    assert code == 1 and "rejected" in err and "ATATT-WRONG" not in out + err


def test_pipeline_list_shows_type_site_and_jql_but_never_the_token(brain, fake, capsys):
    run(capsys, brain, "pipeline", "add", "p", *JIRA)
    code, out, _ = run(capsys, brain, "pipeline", "list")
    assert "jira" in out and SITE in out and "project = MD" in out and TOKEN not in out and EMAIL not in out
    code, out, _ = run(capsys, brain, "pipeline", "list", "--json")
    src = json.loads(out)[0]["sources"][0]
    assert src["type"] == "jira" and src["site"] == SITE and src["jql"] == "project = MD" and TOKEN not in out and EMAIL not in out


def test_dry_run_lists_planned_creates_grouped_by_source_and_writes_nothing(brain, fake, capsys):
    run(capsys, brain, "pipeline", "add", "p", *JIRA)
    code, out, _ = run(capsys, brain, "sync", "p", "--dry-run")
    assert code == 0 and "dry run" in out and "jira  " + SITE in out and out.count("create ") >= 3 and "created 3" in out
    assert not (brain.root / "raw" / "p").exists()


def test_editing_the_jql_of_a_saved_source(brain, fake, capsys):
    run(capsys, brain, "pipeline", "add", "p", *JIRA)
    code, _, err = run(capsys, brain, "pipeline", "edit", "p", "--jira-label", "jira", "--jira-jql", "project = MP")
    assert code == 0, err
    assert load_config(brain).get("p").sources[0].options["jql"] == "project = MP"
    code, _, err = run(capsys, brain, "pipeline", "edit", "p", "--jira-label", "jira", "--jira-jql", "project = = =")
    assert code == 2 and load_config(brain).get("p").sources[0].options["jql"] == "project = MP"


def test_adding_a_second_jira_source_by_edit_needs_all_fields(brain, fake, capsys):
    run(capsys, brain, "pipeline", "add", "p", *JIRA)
    code, _, err = run(capsys, brain, "pipeline", "edit", "p", "--jira-label", "other", "--jira-jql", "project = MP")
    assert code == 2 and "--jira-site" in err
    code, _, err = run(capsys, brain, "pipeline", "edit", "p", "--jira-label", "other", "--jira-site", SITE, "--jira-jql", "project = MP",
                       "--jira-email", "env:JIRA_EMAIL", "--jira-token", "env:JIRA_TOKEN")
    assert code == 0, err and [s.label for s in load_config(brain).get("p").sources] == ["jira", "other"]
