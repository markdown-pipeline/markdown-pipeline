"""MD-116 (FR-S2 AC4): reads by an agent through the MCP server are counted per day: a count and nothing else."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from mcp.client import Client

from conftest import write
from mdpipeline import usage
from mdpipeline.mcp_server import build_server
from mdpipeline.usage import UsageStore

CANARY_QUERY = "ACME-secret-roadmap"


@pytest.fixture
def populated(brain_dir, brain):
    write(brain_dir / "raw/p/pricing.md", "---\nprovenance:\n  pipeline: p\n  source_path: pricing.md\n---\n# Pricing\nusage based pricing\n")
    return brain


def call(brain, *calls):
    async def go():
        async with Client(build_server(brain)) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    return asyncio.run(go())


def stored() -> dict:
    return json.loads((usage.data_dir() / "usage.json").read_text())


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def test_search_read_and_list_each_count_as_one_agent_read(populated, sharing):
    call(populated, ("search", {"query": "pricing"}), ("read", {"path": "raw/p/pricing.md"}), ("list", {}))
    assert stored()["agent_reads"][today()] == 3


def test_status_and_sync_are_not_reads(populated, sharing):
    call(populated, ("sync_status", {}), ("sync", {}))
    assert "agent_reads" not in stored()


def test_the_very_first_read_queues_first_agent_read_once(populated, sharing):
    call(populated, ("list", {}), ("list", {}))
    assert [e["event"] for e in stored()["queue"]].count("first_agent_read") == 1


def test_without_consent_nothing_is_counted_or_stored(populated):
    call(populated, ("search", {"query": "pricing"}))
    assert "agent_reads" not in stored() and UsageStore().pending == 0


def test_what_the_agent_asked_for_is_never_stored(populated, sharing):
    call(populated, ("search", {"query": CANARY_QUERY}), ("read", {"path": "raw/p/pricing.md"}))
    assert CANARY_QUERY not in json.dumps(stored()) and "pricing.md" not in json.dumps(stored())


def test_a_failing_search_still_counts_as_an_attempt_to_read_but_returns_its_normal_error(populated, sharing):
    results = call(populated, ("read", {"path": "raw/p/does-not-exist.md"}))
    assert results[0].is_error is True
    assert stored()["agent_reads"][today()] == 1


def test_a_failure_in_the_usage_code_never_breaks_a_tool_call(populated, sharing, monkeypatch):
    monkeypatch.setattr(UsageStore, "_write", lambda self, data: (_ for _ in ()).throw(OSError("disk full")))
    (result,) = call(populated, ("list", {}))
    assert not result.is_error
