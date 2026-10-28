"""MD-74: the live-verification harness for Jira (scripts/live_jira_check.py) is tested against the fake; the 500-issue resume proof runs here."""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from fake_jira import FakeJira, adf_doc, adf_p
from jira_fixtures import populate
from mdpipeline import jira_api

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "live_jira_check.py"
EMAIL, TOKEN = "owner@example.com", "ATATT-live-harness-token"
SITE = "https://acme.atlassian.net"


@pytest.fixture
def live():
    spec = importlib.util.spec_from_file_location("live_jira_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["live_jira_check"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake():
    """A site whose every ADF node type is one the converter handles (the shared corpus also holds a deliberately unknown one)."""
    from jira_fixtures import rich_description

    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.add_issue("MD", "rich", description=rich_description(), comments=["a comment"], attachments=[("d.png", 10)])
    f.add_issue("MP", "plain", description=adf_doc(adf_p("hello")))
    yield f
    jira_api.set_transport(None)


def run(live, fake, monkeypatch, capsys, mode, *extra):
    monkeypatch.setattr(sys, "stdin", io.StringIO(TOKEN + "\n"))
    code = live.main([mode, "--site", SITE, "--email", EMAIL, "--jql", "project in (MD, MP)", "--stdin", *extra], transport=fake)
    return code, capsys.readouterr().out


def test_check_passes_every_automatable_item_against_the_fake_and_lists_the_manual_ones(live, fake, monkeypatch, capsys, tmp_path):
    report = tmp_path / "r.json"
    code, out = run(live, fake, monkeypatch, capsys, "check", "--report", str(report))
    results = {r["item"]: r for r in json.loads(report.read_text())}
    assert code == 0, out
    for item in ("J1", "J1b", "J2", "J3", "J3b", "J4"):
        assert results[item]["status"] == "PASS", (item, results[item])
    assert {i for i, r in results.items() if r["status"] == "MANUAL"} >= {"J6", "J7", "J8", "J9"}
    assert TOKEN not in out and TOKEN not in report.read_text()


def test_check_reports_an_unknown_adf_node_type_as_a_divergence(live, fake, monkeypatch, capsys):
    fake.add_issue("MD", "has a node we do not know", description=adf_doc({"type": "futureWidget", "content": [adf_p()]}))
    code, out = run(live, fake, monkeypatch, capsys, "check")
    assert code == 1 and "futureWidget" in out and "DIVERGENCE" in out


def test_a_wrong_token_is_a_fail_not_a_crash(live, fake, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("ATATT-wrong\n"))
    code = live.main(["check", "--site", SITE, "--email", EMAIL, "--jql", "project = MD", "--stdin"], transport=fake)
    out = capsys.readouterr().out
    assert code == 1 and "FAIL" in out and "ATATT-wrong" not in out


def test_record_writes_a_sanitised_corpus_with_the_structure_and_no_personal_data(live, fake, monkeypatch, capsys, tmp_path):
    fake.add_issue("MD", "Secret roadmap for Acme Corp", description=adf_doc(adf_p("Call jane.doe@acme.com about pricing at https://acme.atlassian.net/browse/MD-1")),
                   comments=["Ping Dana Reviewer"], assignee="Dana Reviewer")
    out_dir = tmp_path / "real"
    code, out = run(live, fake, monkeypatch, capsys, "record", "--out", str(out_dir), "--limit", "50")
    assert code == 0, out
    text = "\n".join(p.read_text() for p in out_dir.rglob("*") if p.is_file())
    for secret in ("Secret roadmap", "jane.doe@acme.com", "Dana Reviewer", "Mia Owner", "acme.atlassian.net", TOKEN, EMAIL, "Ping"):
        assert secret not in text, secret
    sample = json.loads((out_dir / "issues" / "001.json").read_text())
    assert sample["fields"]["summary"] and sample["fields"]["issuetype"] and "content" in sample["fields"]["description"] or sample["fields"].get("description") is None
    assert (out_dir / "README.md").read_text().startswith("# Sanitised real-schema")


def test_the_sanitiser_is_deterministic_keeps_structure_and_replaces_text(live):
    san = live.Sanitizer()
    doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello World 42!", "marks": [{"type": "link", "attrs": {"href": "https://x.test/a"}}]}]}]}
    out = san.walk(doc)
    assert out["content"][0]["content"][0]["text"] == "Xxxxx Xxxxx 00!" and out["content"][0]["content"][0]["marks"][0]["attrs"]["href"].startswith("https://example.invalid/")
    assert live.Sanitizer().walk(doc) == out


def test_perf_proves_the_incremental_timing_path_and_a_killed_backfill_resumes_without_duplicates_or_losses(live, fake, monkeypatch, capsys, tmp_path):
    for n in range(480):
        fake.add_issue("MD", f"bulk issue {n}")
    report = tmp_path / "p.json"
    code, out = run(live, fake, monkeypatch, capsys, "perf", "--changed", "20", "--backfill", "500", "--report", str(report))
    results = {r["item"]: r for r in json.loads(report.read_text())}
    assert code == 0, out
    assert results["P1"]["status"] == "PASS" and results["P2"]["status"] == "PASS"
    assert "distinct keys" in results["P2"]["detail"] and "0 conflicts" in results["P2"]["detail"]


def test_a_query_far_larger_than_the_backfill_target_asks_for_a_narrower_jql(live, fake, monkeypatch, capsys, tmp_path):
    for n in range(10):
        fake.add_issue("MD", f"more {n}")
    report = tmp_path / "p.json"
    code, out = run(live, fake, monkeypatch, capsys, "perf", "--changed", "2", "--backfill", "2", "--report", str(report))
    assert {r["item"]: r["status"] for r in json.loads(report.read_text())}["P2"] == "MANUAL"


def test_the_script_has_no_token_argument():
    text = SCRIPT.read_text()
    assert '"--token"' not in text


def test_check_names_the_key_and_reason_of_every_issue_that_fails_to_sync_and_prints_no_content(live, fake, monkeypatch, capsys):
    """MD-151 AC1: a failing issue is reported by key + error category/message, never by its content."""
    fake.add_issue("MD", "Customer SECRETWORD roadmap {x}", description={"type": "doc", "content": "not a list"})
    code, out = run(live, fake, monkeypatch, capsys, "check")
    assert code == 1
    assert "J4" in out and "FAIL" in out
    assert "MD-2: file-error" in out and "could not be converted" in out, out
    assert "SECRETWORD" not in out


def test_a_live_check_never_registers_its_scratch_brains_in_the_users_scheduler(live, fake, monkeypatch, capsys):
    """MD-169 (comment 10713): the scratch brains of earlier runs sat in the owner's real scheduler.json and failed every hour."""
    from mdpipeline import engine
    from mdpipeline.schedulestore import SchedulerStore

    before = engine.register_brain
    run(live, fake, monkeypatch, capsys, "check")
    assert SchedulerStore().read()["brains"] == []
    assert engine.register_brain is before          # the opt-out is undone when the script ends
