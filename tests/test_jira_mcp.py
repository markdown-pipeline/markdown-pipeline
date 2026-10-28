"""MD-54: MCP, status and the activity log cover Jira sources (per-source last fetch, search by issue key, jira front-matter)."""
import json

import pytest

from fake_jira import FakeJira, adf_doc, adf_p
from mdpipeline import jira_api
from mdpipeline.brainquery import list_path, read_file, search
from mdpipeline.cli import main
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.status import brain_status
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source
from test_mcp_server import payload, session


def session_inproc(brain, fn):
    """Like ``session`` but the MCP server runs in this process (the fake Jira only exists here)."""
    import asyncio

    from mcp.client import Client

    from mdpipeline.mcp_server import build_server

    async def go():
        async with Client(build_server(brain)) as client:
            return await fn(client)

    return asyncio.run(go())


@pytest.fixture
def world(brain, add_pipeline, monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    jira_api.set_transport(f, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    for n in range(40):
        f.add_issue("MD", f"filler issue number {n}")
    f.add_issue("MD", "Pricing page decision", description=adf_doc(adf_p("We decided on usage based tiers")), comments=["agreed on Friday"])
    add_pipeline(Pipeline("p", [jira_source("project = MD")], guard_threshold_percent=100))
    sync_pipeline(brain, "p", trigger="cli")
    yield brain, f
    jira_api.set_transport(None)


def test_search_finds_a_jira_file_by_issue_key_first(world):
    brain, fake = world
    key = "MD-41"
    hits = search(brain, key)
    assert hits[0]["path"].startswith("raw/p/jira/MD/") and "MD-41" in hits[0]["path"]
    assert hits[0]["provenance"]["issue_key"] == key and hits[0]["provenance"]["source"] == "jira"
    assert search(brain, "md-41")[0]["path"] == hits[0]["path"]


def test_search_finds_words_in_summary_description_and_comments(world):
    brain, _ = world
    for query in ("Pricing page decision", "usage based tiers", "agreed on Friday"):
        assert "MD-41" in search(brain, f'"{query}"')[0]["path"], query


def test_read_returns_the_jira_front_matter_and_provenance(world):
    brain, fake = world
    path = search(brain, "MD-41")[0]["path"]
    doc = read_file(brain, path)
    assert doc["jira"]["key"] == "MD-41" and doc["jira"]["status"] == "To Do" and doc["provenance"]["site"] == SITE
    assert "usage based tiers" in doc["content"]


def test_list_shows_the_jira_provenance_summary(world):
    brain, _ = world
    files = list_path(brain, "raw/p/jira/MD")["files"]
    assert len(files) == 41 and files[0]["provenance"]["issue_key"].startswith("MD-")


def test_mcp_search_and_read_work_on_jira_files(world):
    brain, _ = world

    async def fn(c):
        found = payload(await c.call_tool("search", {"query": "MD-41"}))["results"][0]["path"]
        return found, payload(await c.call_tool("read", {"path": found}))

    path, doc = session(brain, fn)
    assert "MD-41" in path and doc["jira"]["key"] == "MD-41"


def test_mcp_sync_status_shows_per_source_last_fetch_and_last_error_category(world):
    brain, fake = world
    fake.fail("GET", "/search/jql", status=401, times=1)
    sync_pipeline(brain, "p", trigger="cli")

    async def fn(c):
        return payload(await c.call_tool("sync_status", {}))

    data = session(brain, fn)
    src = data["pipelines"][0]["sources"][0]
    assert src["label"] == "jira" and src["type"] == "jira" and src["last_error_category"] == "auth" and src["last_fetch"]
    sync_pipeline(brain, "p", trigger="cli")
    ok = brain_status(brain)["pipelines"][0]["sources"][0]
    assert ok["last_error_category"] is None and ok["last_fetch"] >= src["last_fetch"]


def test_mcp_sync_runs_jira_sources_and_cannot_approve_the_guard(world):
    brain, fake = world
    for n in range(1, 42):
        fake.delete_issue(f"MD-{n}")

    async def fn(c):
        return payload(await c.call_tool("sync", {"pipeline": "p"}))

    res = session_inproc(brain, fn)["results"][0]
    assert res["result"] == "blocked" and "approval needed" in res["message"]


def test_status_and_log_report_per_source_counts_failures_and_warnings_without_content(world, capsys):
    brain, fake = world
    from fake_jira import adf_doc as doc

    fake.add_issue("MD", "weird", description=doc({"type": "futureWidget", "content": [adf_p("SECRET-WIDGET-TEXT")]}))
    sync_pipeline(brain, "p", trigger="cli")
    run = ActivityLog(brain).read(types={"run"})[-1]
    assert run["sources"]["jira"]["created"] == 1 and run["warnings"] == 1
    fake.fail("GET", "/search/jql", status=503, times=1)
    sync_pipeline(brain, "p", trigger="cli")
    capsys.readouterr()
    main(["--brain", str(brain.root), "status"])
    out = capsys.readouterr().out
    assert "jira" in out and "network" in out
    main(["--brain", str(brain.root), "log", "--json"])
    log = capsys.readouterr().out
    assert "SECRET-WIDGET-TEXT" not in log and "usage based tiers" not in log and '"category": "network"' in log
