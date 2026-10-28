"""MD-23: MCP sync_status and sync (the MCP can never approve the guard)."""
import json

import pytest

from conftest import tree_snapshot
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.locking import brain_lock
from mdpipeline.log import ActivityLog
from test_mcp_server import payload, session


@pytest.fixture
def setup(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": f"note {i}" for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    return brain, src


def test_sync_status_returns_last_run_result_counts_conflicts_guard_and_brain_age(setup):
    brain, src = setup
    sync_pipeline(brain, "p")
    (brain.root / "raw/p/docs/n0.md").write_text("edited")
    sync_pipeline(brain, "p")

    async def fn(c):
        return payload(await c.call_tool("sync_status", {}))

    data = session(brain, fn)
    p = data["pipelines"][0]
    assert p["name"] == "p" and p["status"] == "conflicts"
    assert p["last_run"]["result"] == "conflicts" and p["last_run"]["counts"]["conflicts"] == 1
    assert p["conflicts"] == [{"type": "b", "path": "raw/p/docs/n0.md"}]
    assert p["guard"]["blocked"] is False
    assert p["age_seconds"] is not None and data["brain_age_seconds"] is not None


def test_sync_status_for_one_pipeline_and_never_synced(setup):
    brain, _ = setup

    async def fn(c):
        return payload(await c.call_tool("sync_status", {"pipeline": "p"}))

    p = session(brain, fn)["pipelines"][0]
    assert p["status"] == "never" and p["last_run"] is None


def test_sync_runs_the_pipeline_returns_the_cli_result_shape_and_logs_trigger_mcp(setup):
    brain, _ = setup

    async def fn(c):
        return payload(await c.call_tool("sync", {"pipeline": "p"}))

    out = session(brain, fn)
    r = out["results"][0]
    cli_keys = set(sync_pipeline(brain, "p", dry_run=True).to_dict())
    assert cli_keys <= set(r) | {"dry_run"}
    assert r["result"] == "ok" and r["counts"]["created"] == 10 and r["pipeline"] == "p"
    assert (brain.root / "raw/p/docs/n3.md").exists()
    runs = ActivityLog(brain).read(types={"run"})
    assert runs[0]["trigger"] == "mcp"


def test_sync_without_a_pipeline_syncs_all(setup, make_source, add_pipeline):
    brain, _ = setup
    add_pipeline(Pipeline.new("q", [str(make_source("other", {"o.md": "o"}))]))

    async def fn(c):
        return payload(await c.call_tool("sync", {}))

    out = session(brain, fn)
    assert {r["pipeline"]: r["result"] for r in out["results"]} == {"p": "ok", "q": "ok"}


def test_guard_blocked_returns_blocked_with_counts_and_the_message_and_writes_nothing(setup):
    brain, src = setup
    sync_pipeline(brain, "p")
    for i in range(6):
        (src / f"n{i}.md").unlink()
    before = tree_snapshot(brain.raw_dir)

    async def fn(c):
        return payload(await c.call_tool("sync", {"pipeline": "p"}))

    r = session(brain, fn)["results"][0]
    assert r["result"] == "blocked" and r["message"] == "approval needed in UI or CLI"
    assert r["guard"]["count"] == 6 and r["guard"]["total"] == 10 and r["guard"]["percent"] == 60.0
    assert len(r["affected_files"]) == 6
    assert tree_snapshot(brain.raw_dir) == before


def test_the_mcp_has_no_approve_or_force_option_and_cannot_approve_even_if_asked(setup):
    brain, src = setup
    sync_pipeline(brain, "p")
    for i in range(6):
        (src / f"n{i}.md").unlink()
    sync_pipeline(brain, "p")                      # the owner's CLI showed the change set
    before = tree_snapshot(brain.raw_dir)

    async def fn(c):
        tools = {t.name: t for t in (await c.list_tools()).tools}
        props = set(tools["sync"].input_schema.get("properties", {}))
        attempts = []
        for args in ({"pipeline": "p", "approve": True}, {"pipeline": "p", "force": True}):
            res = await c.call_tool("sync", args)
            attempts.append(res)
        return props, attempts

    props, attempts = session(brain, fn)
    assert props == {"pipeline"}
    assert tree_snapshot(brain.raw_dir) == before
    for res in attempts:
        if not res.is_error:
            assert payload(res)["results"][0]["result"] == "blocked"


def test_a_paused_pipeline_returns_skipped_paused(setup):
    brain, _ = setup
    cfg = load_config(brain)
    cfg.pipelines[0].paused = True
    save_config(brain, cfg)

    async def fn(c):
        return payload(await c.call_tool("sync", {"pipeline": "p"}))

    assert session(brain, fn)["results"][0]["result"] == "skipped: paused"
    assert not (brain.root / "raw/p").exists()


def test_unknown_pipeline_is_a_tool_error(setup):
    brain, _ = setup

    async def fn(c):
        return await c.call_tool("sync", {"pipeline": "nope"})

    assert session(brain, fn).is_error


def test_overlapping_sync_returns_busy(setup):
    brain, _ = setup

    async def fn(c):
        return payload(await c.call_tool("sync", {"pipeline": "p"}))

    with brain_lock(brain):
        r = session(brain, fn)["results"][0]
    assert r["result"] == "busy"


def test_status_and_sync_calls_are_logged_by_name(setup):
    brain, _ = setup

    async def fn(c):
        await c.call_tool("sync_status", {})
        await c.call_tool("sync", {"pipeline": "p"})

    session(brain, fn)
    assert [c["tool"] for c in ActivityLog(brain).read(types={"mcp_call"})] == ["sync_status", "sync"]


def test_results_stay_compact_unchanged_files_are_not_listed(setup):
    brain, _ = setup
    sync_pipeline(brain, "p")

    async def fn(c):
        return payload(await c.call_tool("sync", {"pipeline": "p"}))

    r = session(brain, fn)["results"][0]
    assert r["counts"]["unchanged"] == 10
    assert all(i["action"] != "unchanged" for g in r["plan"]["groups"] for i in g["items"])


# --- MD-122: recent_changes ---------------------------------------------------------------------------------------
def test_recent_changes_answers_what_changed_since_yesterday(setup):
    brain, src = setup
    sync_pipeline(brain, "p")
    (src / "n0.md").write_text("edited")
    sync_pipeline(brain, "p")

    async def fn(c):
        return payload(await c.call_tool("recent_changes", {"since": "2000-01-01"}))

    data = session(brain, fn)
    assert data["counts"] == {"new": 10, "changed": 0, "deleted": 0}      # n0 is new then edited: still one new file
    assert len(data["items"]) == 10 and data["syncs"] == 2


def test_recent_changes_with_no_arguments_covers_the_last_day_and_is_read_only(setup):
    brain, _ = setup
    sync_pipeline(brain, "p")
    before = tree_snapshot(brain.root)

    async def fn(c):
        return payload(await c.call_tool("recent_changes", {}))

    assert session(brain, fn)["counts"]["new"] == 10
    assert {k for k in tree_snapshot(brain.root) if k not in before} <= {".mdpipeline/log.jsonl"}


def test_recent_changes_with_a_bad_time_is_a_tool_error(setup):
    brain, _ = setup

    async def fn(c):
        return await c.call_tool("recent_changes", {"since": "soon"})

    res = session(brain, fn)
    assert res.is_error


def test_recent_changes_is_logged_by_name_only(setup):
    brain, _ = setup

    async def fn(c):
        await c.call_tool("recent_changes", {"since": "2000-01-01"})

    session(brain, fn)
    assert [e["tool"] for e in ActivityLog(brain).read(types={"mcp_call"})] == ["recent_changes"]
