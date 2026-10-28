"""MD-101: MCP, status and Activity are correct for a pipeline without a folder source (a Jira-only pipeline)."""
import asyncio
import json

import pytest

from fake_jira import FakeJira
from mcp.client import Client
from mdpipeline import jira_api
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, load_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.mcp_server import build_server
from mdpipeline.status import brain_status
from mdpipeline.uiactivity import activity_view
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from uihelper import running_ui


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


@pytest.fixture
def world(brain, fake, add_pipeline):
    add_pipeline(Pipeline("p", [jira_source(label="md-jira")], name_policy="date-name", guard_threshold_percent=100))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    return type("W", (), {"brain": brain, "fake": fake})


def mcp(brain, tool, args=None):
    async def call():
        async with Client(build_server(brain)) as client:
            result = await client.call_tool(tool, args or {})
            return json.loads(result.content[0].text)

    return asyncio.run(call())


def test_mcp_status_describes_the_pipeline_by_source_type_with_no_folder_field(world):
    [p] = mcp(world.brain, "sync_status")["pipelines"]
    assert p["status"] == "ok" and [(s["label"], s["type"]) for s in p["sources"]] == [("md-jira", "jira")]
    assert not any("folder" in k for k in p) and "path" not in json.dumps(p["sources"])


def test_mcp_list_search_and_sync_work_for_a_jira_only_brain(world):
    assert mcp(world.brain, "list")["folders"][0]["path"] == "raw"
    [hit, *_] = mcp(world.brain, "search", {"query": "issue 0"})["results"]
    assert hit["provenance"]["source"] == "jira" and hit["provenance"]["source_label"] == "md-jira" and hit["provenance"]["site"] == SITE
    [res] = mcp(world.brain, "sync")["results"]
    assert res["result"] == "ok" and res["counts"]["unchanged"] == 3 and res["plan"]["groups"][0]["label"] == "md-jira"


def test_cli_status_and_the_ui_home_row_describe_the_jira_source(world, capsys):
    assert main(["--brain", str(world.brain.root), "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert json.dumps(status).count("md-jira") >= 1
    with running_ui(world.brain) as ui:
        row = ui.get("/api/state")[1]["pipelines"][0]
    assert row["sources"][0]["type"] == "jira" and "Jira" in row["folders_text"] and row["status"] == "ok"


@pytest.mark.parametrize("failure,category", [(401, "auth"), (503, "network"), (429, "rate-limited")])
def test_a_failing_jira_only_pipeline_is_an_error_with_jira_wording_and_nothing_deleted(world, failure, category):
    owned_before = sorted(p.name for p in (world.brain.root / "raw/p/md-jira").rglob("*.md"))
    world.fake.fail("GET", "/search/jql", status=failure, times=9)
    res = sync_pipeline(world.brain, "p", trigger="ui")
    assert res.result == "error"
    [s] = brain_status(world.brain)["pipelines"]
    assert s["status"] == "error"
    [card] = [c for c in activity_view(world.brain)["needs_you"] if c["kind"] == "error"]
    assert card["connectors"][0]["category"] == category and card["folders"] == [] and card["file_errors"] == []
    assert sorted(p.name for p in (world.brain.root / "raw/p/md-jira").rglob("*.md")) == owned_before          # nothing deleted
    assert load_config(world.brain).get("p").sources[0].type == "jira"
