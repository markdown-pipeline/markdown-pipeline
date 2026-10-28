"""MD-99: choose source types in the editor; a pipeline can have Jira (or a folder) as its only source (backend of the editor)."""
import json

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from uihelper import running_ui

JIRA_DRAFT = {"type": "jira", "label": "jira", "site": SITE, "jql": "project = MD", "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"}


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


def draft(sources, **kw):
    d = {"name": "p", "sources": sources, "target": "raw/{pipeline}/{source}/{relpath}", "include": ["**/*.md"], "exclude": [".git/**"],
         "on_source_delete": "delete", "guard_threshold_percent": 40, "guard_min_files": 5, "name_policy": "original"}
    d.update(kw)
    return d


def folder(path, label):
    return {"path": str(path), "label": label}


def test_zero_sources_is_refused_with_a_plain_message_in_the_preview_and_the_save(brain):
    with running_ui(brain) as ui:
        _, pv = ui.post("/api/preview", {"draft": draft([]), "editing": None})
        status, res = ui.post("/api/pipelines", {"draft": draft([])})
    [err] = [e for e in pv["validation"]["errors"] if e["code"] == "no-sources"]
    assert err["message"] == "Add at least one source." and pv["validation"]["can_save"] is False
    assert status == 422 and load_config(brain).get("p") is None


def test_a_jira_only_pipeline_is_saved_and_synced_from_the_ui(brain, fake):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/pipelines", {"draft": draft([JIRA_DRAFT]), "sync": True})
    assert status == 200 and res["sync"]["result"] == "ok" and res["sync"]["counts"]["created"] == 3
    assert [s.type for s in load_config(brain).get("p").sources] == ["jira"]


def test_switching_a_saved_folder_pipeline_to_jira_only_keeps_the_folders_files_unowned(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(6)})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")], name_policy="original", guard_threshold_percent=100))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([JIRA_DRAFT]), "remove_folders": [{"label": "docs", "delete_files": False}]})
    assert status == 200, res
    assert [s.type for s in load_config(brain).get("p").sources] == ["jira"]
    assert len(list((brain.root / "raw/p/docs").glob("*.md"))) == 6                  # kept
    assert not any(t.startswith("raw/p/docs/") for t in State.load(brain).pipeline("p").files)        # ...and owned by nobody


def test_switching_to_jira_only_with_the_delete_opt_in_goes_through_the_guard(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(8)})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")], name_policy="original"))
    sync_pipeline(brain, "p", trigger="cli")
    body = {"draft": draft([JIRA_DRAFT]), "remove_folders": [{"label": "docs", "delete_files": True}]}
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", body)
        assert status == 409 and res["result"] == "blocked" and res["guard"]["count"] == 8
        assert [s.type for s in load_config(brain).get("p").sources] == ["folder"]              # nothing changed
        status, res = ui.put("/api/pipelines/p", {**body, "approve": True})
    assert status == 200 and not list((brain.root / "raw/p/docs").glob("*.md"))


def test_a_failing_jira_source_stops_the_switch_before_anything_is_removed(brain, fake, add_pipeline, make_source, monkeypatch):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(6)})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    monkeypatch.setenv("JIRA_TOKEN", "ATATT-WRONG-TOKEN-VALUE")
    before = brain.config_path.read_text()
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([JIRA_DRAFT]), "remove_folders": [{"label": "docs", "delete_files": True}]})
    assert status == 422 and brain.config_path.read_text() == before
    assert len(list((brain.root / "raw/p/docs").glob("*.md"))) == 6 and len(State.load(brain).pipeline("p").files) == 6


def test_removing_the_last_source_without_a_replacement_is_refused(brain, add_pipeline, make_source):
    docs = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")]))
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([]), "remove_folders": [{"label": "docs", "delete_files": False}]})
    assert status == 422 and "Add at least one source" in json.dumps(res) and [s.label for s in load_config(brain).get("p").sources] == ["docs"]


def test_home_rows_and_the_listing_name_each_source_with_its_type(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline("mixed", [Source(str(docs), "docs"), jira_source(label="md-jira")]))
    add_pipeline(Pipeline("solo", [jira_source(label="md-jira")]))
    with running_ui(brain) as ui:
        rows = {r["name"]: r for r in ui.get("/api/state")[1]["pipelines"]}
    assert rows["mixed"]["folders_text"] == "2 sources: docs (folder), md-jira (Jira)"
    assert "md-jira" in rows["solo"]["folders_text"] and SITE in rows["solo"]["folders_text"] and TOKEN not in json.dumps(rows)


def test_typed_jira_credentials_that_fail_stop_the_switch_before_anything_is_removed(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(6)})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    before = brain.config_path.read_text()
    typed = {"type": "jira", "label": "jira", "site": SITE, "jql": "project = MD", "email": EMAIL, "token": "ATATT-WRONG-TOKEN-VALUE"}
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([typed]), "remove_folders": [{"label": "docs", "delete_files": True}]})
    assert status == 422 and brain.config_path.read_text() == before and "ATATT-WRONG" not in json.dumps(res)
    assert len(list((brain.root / "raw/p/docs").glob("*.md"))) == 6 and len(State.load(brain).pipeline("p").files) == 6


def test_typed_jira_credentials_that_pass_complete_the_switch(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": str(i) for i in range(6)})
    add_pipeline(Pipeline.new("p", [Source(str(docs), "docs")], name_policy="original", guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    typed = {"type": "jira", "label": "jira", "site": SITE, "jql": "project = MD", "email": EMAIL, "token": TOKEN}
    with running_ui(brain) as ui:
        status, res = ui.put("/api/pipelines/p", {"draft": draft([typed]), "remove_folders": [{"label": "docs", "delete_files": False}]})
    assert status == 200, res
    src = load_config(brain).get("p").sources[0]
    assert src.type == "jira" and src.options["token_ref"].startswith("vault:") and TOKEN not in brain.config_path.read_text()
