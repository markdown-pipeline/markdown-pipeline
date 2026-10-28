"""MD-22: the MCP server (real stdio subprocess, as an agent client would start it)."""
import asyncio
import json
import os
import sys

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import tree_snapshot, write
from mdpipeline.log import ActivityLog


def params(brain):
    # The client hands a child only a short list of variables unless told otherwise; the child must see the test's data dir (MD-179: on Windows it did not)
    return StdioServerParameters(command=sys.executable, args=["-m", "mdpipeline", "--brain", str(brain.root), "mcp"], env=dict(os.environ))


def session(brain, fn):
    async def go():
        async with Client(params(brain)) as client:
            return await fn(client)

    return asyncio.run(go())


def payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


@pytest.fixture
def populated(brain_dir, brain):
    write(brain_dir / "raw/p/pricing.md", "---\nprovenance:\n  pipeline: p\n  source_path: pricing.md\n---\n# Pricing\nusage based pricing\n")
    write(brain_dir / "wiki/guard.md", "# Guard\nthe guard protects\n")
    write(brain_dir / "work/secret.md", "pricing secret\n")
    return brain


def test_server_starts_on_demand_and_lists_its_tools(populated):
    async def fn(c):
        return sorted(t.name for t in (await c.list_tools()).tools)

    names = session(populated, fn)
    assert {"search", "read", "list"} <= set(names)


def test_no_tool_can_create_modify_or_delete_brain_content(populated):
    async def fn(c):
        return [t.name for t in (await c.list_tools()).tools]

    names = session(populated, fn)
    assert not [n for n in names if any(w in n for w in ("write", "edit", "delete", "create", "remove", "update", "save"))]
    assert set(names) <= {"search", "read", "list", "sync_status", "sync", "recent_changes"}


def test_search_read_list_work_through_the_protocol(populated):
    async def fn(c):
        s = payload(await c.call_tool("search", {"query": "pricing"}))
        r = payload(await c.call_tool("read", {"path": "raw/p/pricing.md"}))
        l = payload(await c.call_tool("list", {"path_prefix": "raw/p"}))
        return s, r, l

    s, r, l = session(populated, fn)
    assert [x["path"] for x in s["results"]] == ["raw/p/pricing.md"]
    assert s["results"][0]["provenance"]["pipeline"] == "p"
    assert "usage based pricing" in r["content"] and r["provenance"]["pipeline"] == "p"
    assert [f["path"] for f in l["files"]] == ["raw/p/pricing.md"]


def test_search_path_prefix_and_limit_parameters(populated):
    async def fn(c):
        return payload(await c.call_tool("search", {"query": "guard", "path_prefix": "wiki", "limit": 1}))

    assert [x["path"] for x in session(populated, fn)["results"]] == ["wiki/guard.md"]


def test_out_of_scope_and_outside_paths_are_refused_as_tool_errors(populated):
    async def fn(c):
        out = []
        for tool, args in (("read", {"path": "work/secret.md"}), ("read", {"path": "../../etc/passwd"}),
                           ("read", {"path": "/etc/passwd"}), ("list", {"path_prefix": "work"}),
                           ("search", {"query": "x", "path_prefix": ".mdpipeline"})):
            res = await c.call_tool(tool, args)
            out.append(res.is_error)
        return out

    assert session(populated, fn) == [True] * 5


def test_anticipated_errors_reach_the_agent_with_their_message(populated):
    # a missing path is a normal agent mistake, not a crash: the agent must see why it failed
    async def fn(c):
        out = []
        for tool, args in (("read", {"path": "raw/missing/x.md"}), ("list", {"path_prefix": "raw/missing"}),
                           ("search", {"query": "x", "path_prefix": "raw/missing"}), ("read", {"path": "work/secret.md"}),
                           ("search", {"query": "  "}), ("sync", {"pipeline": "nope"})):
            res = await c.call_tool(tool, args)
            out.append((res.is_error, res.content[0].text))
        return out

    results = session(populated, fn)
    assert all(is_error for is_error, _ in results)
    texts = [t for _, t in results]
    assert "Not found: raw/missing/x.md" in texts[0]
    assert "Not found: raw/missing" in texts[1] and "Not found: raw/missing" in texts[2]
    assert "only files a pipeline wrote, and raw/ and wiki/, are readable" in texts[3]
    assert "query is empty" in texts[4]
    assert "No pipeline named 'nope'" in texts[5]
    assert all("list" in t for t in texts[:3])  # a not-found error points the agent to the list tool


def test_calls_never_modify_the_brain(populated, brain_dir):
    before = {k: v for k, v in tree_snapshot(brain_dir).items() if ".mdpipeline" not in k}

    async def fn(c):
        await c.call_tool("search", {"query": "pricing"})
        await c.call_tool("read", {"path": "raw/p/pricing.md"})
        await c.call_tool("list", {})
        await c.call_tool("read", {"path": "../x"})

    session(populated, fn)
    after = {k: v for k, v in tree_snapshot(brain_dir).items() if ".mdpipeline" not in k}
    assert after == before


def test_every_call_is_logged_by_tool_name_and_time_without_the_query(populated):
    async def fn(c):
        await c.call_tool("search", {"query": "very-secret-query-text"})
        await c.call_tool("read", {"path": "raw/p/pricing.md"})
        await c.call_tool("list", {})

    session(populated, fn)
    calls = ActivityLog(populated).read(types={"mcp_call"})
    assert [c["tool"] for c in calls] == ["search", "read", "list"]
    assert all("ts" in c for c in calls)
    assert "very-secret-query-text" not in (populated.config_dir / "log.jsonl").read_text()
    assert "pricing.md" not in (populated.config_dir / "log.jsonl").read_text()


def test_the_server_prints_nothing_but_protocol_on_stdout_and_needs_no_network(populated):
    # an in-process check: building the server must not need a daemon, port or network
    from mdpipeline.mcp_server import build_server

    server = build_server(populated)
    assert server.name == "mdpipeline"
