"""MD-53: the UI adds and edits a Jira source with Test connection, a live dry run, and clear "Needs you" errors; the token never reaches the browser."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from uihelper import running_ui

DRAFT_SOURCE = {"type": "jira", "label": "jira", "site": SITE, "jql": "project = MD", "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"}


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    for n in range(3):
        f.add_issue("MD", f"issue {n}")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


def draft(name="p", **extra):
    return {"name": name, "sources": [DRAFT_SOURCE], "name_policy": "date-name", **extra}


def test_test_connection_returns_the_user_and_the_match_count(brain, fake):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/jira-test", DRAFT_SOURCE)
    assert status == 200 and res["ok"] is True and res["user"] == "Mia Owner" and res["matches"] == 3 and TOKEN not in json.dumps(res)


@pytest.mark.parametrize("override,category", [({"token_ref": "env:NOT_SET_ANYWHERE"}, "auth"), ({"jql": "project = = ="}, "bad-query"),
                                               ({"site": "https://evil.example.com"}, "bad-jira-site"), ({"token_ref": TOKEN}, "bad-jira-credential")])
def test_test_connection_failures_are_plain_with_a_category_and_never_echo_a_secret(brain, fake, override, category):
    with running_ui(brain) as ui:
        res = ui.post("/api/jira-test", {**DRAFT_SOURCE, **override})[1]
    assert res["ok"] is False and res["errors"][0]["category"] == category and TOKEN not in json.dumps(res)


def test_a_rejected_token_in_test_connection(brain, fake, monkeypatch):
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    with running_ui(brain) as ui:
        res = ui.post("/api/jira-test", DRAFT_SOURCE)[1]
    assert res["errors"][0]["category"] == "auth" and "rejected" in res["errors"][0]["message"] and "ATATT-WRONG" not in json.dumps(res)


def test_the_live_preview_works_for_a_jira_source_and_writes_nothing(brain, fake):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/preview", {"draft": draft(), "editing": None})
    assert status == 200 and res["preview"]["counts"]["create"] == 3 and res["preview"]["groups"][0]["label"] == "jira"
    assert res["validation"]["can_save"] is True and not (brain.root / "raw" / "p").exists()
    assert [r for r in fake.requests if r[0] != "GET" and not r[1].endswith("approximate-count")] == []


def test_the_preview_reports_a_failing_site_as_a_group_error_not_a_crash(brain, fake):
    fake.fail("GET", "/search/jql", status=503, times=9)
    with running_ui(brain) as ui:
        res = ui.post("/api/preview", {"draft": draft(), "editing": None})[1]
    group = res["preview"]["groups"][0]
    assert group["status"] == "unreadable" and "not available" in group["error"]


def test_saving_validates_against_jira_and_saves_nothing_on_failure(brain, fake, monkeypatch):
    with running_ui(brain) as ui:
        monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
        status, res = ui.post("/api/pipelines", {"draft": draft()})
        assert status == 422 and "rejected" in json.dumps(res) and load_config(brain).get("p") is None
        monkeypatch.setenv("JIRA_TOKEN", TOKEN)
        status, res = ui.post("/api/pipelines", {"draft": draft(), "sync": True})
        assert status == 200 and res["saved"] and res["sync"]["result"] == "ok" and res["sync"]["counts"]["created"] == 3
    src = load_config(brain).get("p").sources[0]
    assert src.type == "jira" and src.options["token_ref"] == "env:JIRA_TOKEN" and TOKEN not in brain.config_path.read_text()


def test_the_editor_loads_a_saved_jira_source_with_references_only(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name"))
    with running_ui(brain) as ui:
        view = ui.get("/api/pipelines/p")[1]
        assert view["draft"]["sources"][0] == {**DRAFT_SOURCE, "path": SITE, "enabled": True}
        assert TOKEN not in json.dumps(view) and EMAIL not in json.dumps(view)


def test_home_rows_describe_a_jira_source(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name"))
    with running_ui(brain) as ui:
        row = ui.get("/api/state")[1]["pipelines"][0]
    assert SITE in row["folders_text"] and "project = MD" in row["folders_text"] and row["sources"][0]["type"] == "jira"


@pytest.mark.parametrize("failure,category,needle", [((401,), "auth", "JIRA_TOKEN"), ((403,), "permission", "permission"), ((503,), "network", "try again"),
                                                    ((404,), "site-not-found", "site URL")])
def test_needs_you_explains_each_jira_failure_with_the_source_category_and_next_step(brain, fake, add_pipeline, failure, category, needle):
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    fake.fail("GET", "/search/jql", status=failure[0], times=1)
    sync_pipeline(brain, "p", trigger="ui")
    with running_ui(brain) as ui:
        act = ui.get("/api/activity")[1]
        card = next(c for c in act["needs_you"] if c["kind"] == "error")
        c = card["connectors"][0]
        assert c["label"] == "jira" and c["category"] == category and needle in c["next_step"] and c["site"] == SITE
        assert act["badge"] >= 1 and TOKEN not in json.dumps(act) and EMAIL not in json.dumps(act)
        row = ui.get("/api/state")[1]["pipelines"][0]
        assert row["status"] == "error" and "Nothing it owns was deleted" not in row["summary"]


def test_a_failed_conversion_of_one_issue_is_a_file_error_not_a_source_failure(brain, fake, add_pipeline):
    from fake_jira import adf_doc

    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name"))
    fake.add_issue("MD", "broken", description={"type": "doc", "content": "not a list"})
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["created"] == 3 and res.counts()["errors"] == 1
    with running_ui(brain) as ui:
        card = next(c for c in ui.get("/api/activity")[1]["needs_you"] if c["kind"] == "error")
        assert card["file_errors"] and "could not be converted" in card["file_errors"][0]["error"] and "MD-4" in card["file_errors"][0]["error"]


def test_partial_failure_card_counts_failures_names_keys_and_says_what_was_kept(brain, fake, add_pipeline):
    """MD-151 AC3: some issues fail, others succeed -> how many failed, which keys, others written, nothing deleted, next step."""
    add_pipeline(Pipeline("p", [jira_source()], name_policy="date-name"))
    fake.add_issue("MD", "broken", description={"type": "doc", "content": "not a list"})
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["errors"] == 1
    with running_ui(brain) as ui:
        card = next(c for c in ui.get("/api/activity")[1]["needs_you"] if c["kind"] == "error")
        assert card["title"] == "1 file could not be copied"
        assert [f["path"] for f in card["file_errors"]] == ["MD-4.md"] or "MD-4" in card["file_errors"][0]["path"]
        assert card["files_written"] == res.counts()["created"] == 3
        assert "Sync now" in card["file_errors_next_step"] and "report" in card["file_errors_next_step"].lower()
        view = ui.get("/api/pipelines/p/view")[1]
        assert any(c["kind"] == "error" and c["files_written"] == 3 for c in view["needs_you"])


@pytest.mark.parametrize("policy", ["original", "date-name"])
def test_an_issue_title_with_braces_syncs_instead_of_failing_the_run(brain, fake, add_pipeline, policy):
    """MD-151 AC4: the real cause was '{...}' in a Jira title being re-parsed as template syntax (live: 2 of 207 failed every sync)."""
    add_pipeline(Pipeline("p", [jira_source()], name_policy=policy))
    fake.add_issue("MD", "Fix {broken} parser and {unclosed", description=None)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["errors"] == 0 and res.result == "ok", res.errors
    assert any("Fix {broken} parser" in f.read_text() for f in (brain.root / "raw").rglob("*.md"))
