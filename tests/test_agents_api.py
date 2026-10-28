"""MD-120: the agents view, connect / disconnect through the UI server, the usage event, and the local read count (AC7)."""
import json

import pytest

from mdpipeline import agents, usage
from mdpipeline.config import Pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.usage import UsageStore
from test_agents import bin_dir, calls, install  # noqa: F401
from uihelper import running_ui


@pytest.fixture
def synced(brain, add_pipeline, tmp_path):
    out = tmp_path / "out" / "acme-jira"
    (out / "ACME").mkdir(parents=True)
    (out / "ACME" / "ACME-1.md").write_text("---\njira:\n  key: ACME-1\n  status_category: In Progress\n---\n# ACME-1: Pay\n")
    p = Pipeline.new("acme-jira", [])
    p.destination = {"type": "directory", "path": str(out)}
    add_pipeline(p)
    return out


def queued(*names):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def test_the_view_lists_found_agents_the_exact_change_and_the_suggested_question(bin_dir, brain, synced):
    install(bin_dir, "claude")
    install(bin_dir, "codex")
    with running_ui(brain) as ui:
        status, view = ui.get("/api/agents?pipeline=acme-jira")
    assert status == 200 and [a["agent"] for a in view["found"]] == ["claude_code", "codex"]
    assert view["found"][0]["label"] == "Claude Code" and view["found"][0]["exact"][0].startswith("claude mcp add --scope user ")
    assert view["question"]["question"] == "What is in progress in ACME right now, and what is blocked?"
    assert view["commands"]["claude_code"] == "claude 'What is in progress in ACME right now, and what is blocked?'"
    assert view["folder"] == str(synced) and view["connected"] == {}


def test_without_any_agent_the_view_carries_copy_paste_instructions_for_the_others(bin_dir, brain, synced):
    with running_ui(brain) as ui:
        _, view = ui.get("/api/agents?pipeline=acme-jira")
    assert view["found"] == [] and set(view["manual"]) >= {"claude_code_command", "claude_code_json", "codex_toml"}


def test_connect_registers_the_agent_writes_the_file_and_queues_agent_connected(bin_dir, brain, synced):
    install(bin_dir, "claude")
    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        status, res = ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira"})
    assert status == 200 and res["ok"] is True and (synced / "AGENTS.md").exists()
    (event,) = queued("agent_connected")
    assert event["properties"]["agent"] == "claude_code" and event["properties"]["method"] == "auto"


def test_connect_without_consent_sends_nothing(bin_dir, brain, synced):
    install(bin_dir, "claude")
    with running_ui(brain) as ui:
        ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira"})
    assert queued("agent_connected") == []


def test_an_existing_agents_md_comes_back_as_a_choice_not_an_overwrite(bin_dir, brain, synced):
    install(bin_dir, "claude")
    (synced / "AGENTS.md").write_text("mine")
    with running_ui(brain) as ui:
        status, res = ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira"})
        assert status == 200 and res["conflict"]["alternative"] == "MARKDOWN-PIPELINE.md" and calls(bin_dir) == []
        _, res = ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira", "agents_md": "keep"})
    assert res["ok"] is True and (synced / "AGENTS.md").read_text() == "mine"


def test_a_failed_connect_queues_no_event(bin_dir, brain, synced):
    install(bin_dir, "claude", exit_code=1, stderr="nope")
    UsageStore().set_consent("share")
    with running_ui(brain) as ui:
        _, res = ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira"})
    assert res["ok"] is False and queued("agent_connected") == []


def test_an_unknown_agent_or_pipeline_is_a_400_or_404(bin_dir, brain, synced):
    with running_ui(brain) as ui:
        assert ui.post("/api/agents/connect", {"agent": "emacs", "pipeline": "acme-jira"})[0] == 400
        assert ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "nope"})[0] == 404


def test_disconnect_undoes_the_connect(bin_dir, brain, synced):
    install(bin_dir, "claude")
    with running_ui(brain) as ui:
        ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira"})
        status, res = ui.post("/api/agents/disconnect", {"agent": "claude_code"})
        _, view = ui.get("/api/agents?pipeline=acme-jira")
    assert status == 200 and res["ok"] is True and not (synced / "AGENTS.md").exists() and view["connected"] == {}


def test_the_view_of_a_connected_agent_shows_what_was_changed(bin_dir, brain, synced):
    install(bin_dir, "claude")
    with running_ui(brain) as ui:
        ui.post("/api/agents/connect", {"agent": "claude_code", "pipeline": "acme-jira"})
        _, view = ui.get("/api/agents?pipeline=acme-jira")
    c = view["connected"]["claude_code"]
    assert c["label"] == "Claude Code" and c["changes"][0].startswith("claude mcp add") and c["changes"][1] == f"created {synced / 'AGENTS.md'}"
    assert c["reads"] == 0


def test_reads_are_the_search_read_and_list_calls_since_the_connect_not_status_or_sync(bin_dir, brain, synced):
    install(bin_dir, "claude")
    log = ActivityLog(brain)
    log.mcp_call("search")                       # before the connect: not this agent's
    agents.connect(brain, "claude_code", str(synced))
    for tool in ("search", "read", "read", "list", "sync_status", "sync"):
        log.mcp_call(tool)
    assert agents.reads_since_connect(brain, "claude_code") == 4


def test_reads_of_an_agent_that_is_not_connected_are_zero(brain):
    assert agents.reads_since_connect(brain, "codex") == 0
