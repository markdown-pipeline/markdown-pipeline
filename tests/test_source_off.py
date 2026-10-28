"""MD-103: a source can be switched Off and On. An Off source keeps its setup, is not read (no scan, no network, no credential read),
and its files stay owned, are kept and never deleted. Core behaviour (config, planner, engine); UI and CLI have their own files."""
import shutil

import pytest
import yaml

from conftest import tree_snapshot, write
from fake_jira import FakeJira
from mdpipeline import credentials, jira_api
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from mdpipeline.validation import validate_pipeline
from test_jira_sync import EMAIL, SITE, TOKEN, jira_source


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
def world(brain, fake, add_pipeline, make_source):
    docs = make_source("docs", {f"d{i}.md": f"doc {i}" for i in range(6)})
    add_pipeline(Pipeline("p", [Source(str(docs), "docs"), jira_source(label="md-jira")], name_policy="original", guard_threshold_percent=40))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    return type("W", (), {"brain": brain, "docs": docs, "fake": fake})


def set_enabled(brain, label, enabled):
    cfg = load_config(brain)
    next(s for s in cfg.get("p").sources if s.label == label).enabled = enabled
    save_config(brain, cfg)


def owned(brain):
    return sorted(State.load(brain).pipeline("p").files)


# config ------------------------------------------------------------------------------------------------------------------
def test_a_source_is_on_by_default_and_old_configs_load_with_every_source_on(brain, make_source):
    save_config(brain, load_config(brain).__class__([Pipeline.new("p", [str(make_source("d"))])]))
    assert "enabled" not in brain.config_path.read_text()
    assert [s.enabled for s in load_config(brain).get("p").sources] == [True]


def test_an_off_source_keeps_all_its_settings_in_the_config(brain, make_source):
    cfg = load_config(brain)
    cfg.pipelines.append(Pipeline("p", [Source(str(make_source("d")), "docs", enabled=False), jira_source(label="j")]))
    cfg.get("p").sources[1].enabled = False
    save_config(brain, cfg)
    raw = yaml.safe_load(brain.config_path.read_text())["pipelines"][0]["sources"]
    assert raw[0]["enabled"] is False and raw[1]["enabled"] is False and raw[1]["options"]["jql"] == "project = MD"
    loaded = load_config(brain).get("p").sources
    assert [s.enabled for s in loaded] == [False, False] and loaded[1].options["site"] == SITE


# the planner / engine ----------------------------------------------------------------------------------------------------
def test_an_off_folder_is_not_scanned_and_its_files_are_kept_byte_identical_and_owned(world):
    set_enabled(world.brain, "docs", False)
    before_owned = owned(world.brain)
    docs_before = tree_snapshot(world.brain.root / "raw/p/docs")
    shutil.rmtree(world.docs)                              # the folder is gone while Off: nothing may change because of it
    for _ in range(3):
        res = sync_pipeline(world.brain, "p", trigger="cli")
        assert res.result == "ok", res.message
    assert tree_snapshot(world.brain.root / "raw/p/docs") == docs_before
    assert owned(world.brain) == before_owned
    plan = sync_pipeline(world.brain, "p", trigger="cli", dry_run=True).plan
    assert plan.counts()["deleted"] == 0 and plan.counts()["kept"] == 6
    assert {i.reason for i in plan.items() if i.action == "keep"} == {"source off: kept"}


def test_an_off_source_is_not_an_error_or_a_problem(world):
    set_enabled(world.brain, "docs", False)
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "ok" and res.plan.errors == []
    from mdpipeline.status import brain_status

    [s] = brain_status(world.brain)["pipelines"]
    assert s["status"] == "ok" and [(x["label"], x["enabled"]) for x in s["sources"]] == [("docs", False), ("md-jira", True)]


def test_an_off_jira_source_makes_no_request_and_reads_no_credential(world, monkeypatch):
    set_enabled(world.brain, "md-jira", False)
    world.fake.requests.clear()
    monkeypatch.setattr(credentials, "resolve_token", lambda ref: (_ for _ in ()).throw(AssertionError("a credential was read")))
    monkeypatch.setattr("mdpipeline.jira_source.resolve_token", lambda ref: (_ for _ in ()).throw(AssertionError("a credential was read")))
    jira_files = [t for t in owned(world.brain) if "/md-jira/" in t]
    assert jira_files
    for _ in range(2):
        assert sync_pipeline(world.brain, "p", trigger="cli").result == "ok"
    assert world.fake.requests == [] and [t for t in owned(world.brain) if "/md-jira/" in t] == jira_files


def test_the_files_of_an_off_source_still_count_in_the_guard_denominator(world):
    set_enabled(world.brain, "docs", False)
    plan = sync_pipeline(world.brain, "p", trigger="cli", dry_run=True).plan
    assert plan.owned_total == len(owned(world.brain)) == 9


# back On -----------------------------------------------------------------------------------------------------------------
def test_switching_a_folder_back_on_plans_everything_that_changed_while_it_was_off(world):
    set_enabled(world.brain, "docs", False)
    write(world.docs / "d0.md", "doc 0 CHANGED")
    write(world.docs / "new.md", "brand new")
    (world.docs / "d5.md").unlink()
    sync_pipeline(world.brain, "p", trigger="cli")
    set_enabled(world.brain, "docs", True)
    plan = sync_pipeline(world.brain, "p", trigger="cli", dry_run=True).plan
    c = plan.counts()
    assert (c["created"], c["updated"], c["deleted"]) == (1, 1, 1)
    assert sync_pipeline(world.brain, "p", trigger="cli").result == "ok"
    assert not (world.brain.root / "raw/p/docs/d5.md").exists() and "brand new" in (world.brain.root / "raw/p/docs/new.md").read_text()


def test_a_brain_side_edit_made_while_off_becomes_a_conflict_when_the_source_is_back_on(world):
    set_enabled(world.brain, "docs", False)
    target = world.brain.root / "raw/p/docs/d1.md"
    target.write_text(target.read_text() + "\nmy own note")
    (world.docs / "d1.md").unlink()
    sync_pipeline(world.brain, "p", trigger="cli")
    set_enabled(world.brain, "docs", True)
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "conflicts" and "my own note" in target.read_text()


def test_the_guard_applies_when_a_source_comes_back_on_with_most_files_gone(world):
    set_enabled(world.brain, "docs", False)
    for f in world.docs.glob("*.md"):
        f.unlink()
    set_enabled(world.brain, "docs", True)
    res = sync_pipeline(world.brain, "p", trigger="cli")
    assert res.result == "blocked" and len(list((world.brain.root / "raw/p/docs").glob("*.md"))) == 6


def test_a_jira_issue_deleted_while_the_source_was_off_is_found_when_it_is_back_on(world):
    cfg = load_config(world.brain)
    cfg.get("p").guard_threshold_percent = 100
    save_config(world.brain, cfg)
    set_enabled(world.brain, "md-jira", False)
    world.fake.delete_issue("MD-1")
    sync_pipeline(world.brain, "p", trigger="cli")
    before = len([t for t in owned(world.brain) if "/md-jira/" in t])
    set_enabled(world.brain, "md-jira", True)
    assert sync_pipeline(world.brain, "p", trigger="cli", dry_run=True).plan.counts()["deleted"] == 1
    assert sync_pipeline(world.brain, "p", trigger="cli").result == "ok"
    assert len([t for t in owned(world.brain) if "/md-jira/" in t]) == before - 1


# validation --------------------------------------------------------------------------------------------------------------
def test_saving_with_every_source_off_is_refused_with_the_plain_message(world):
    cfg = load_config(world.brain)
    for s in cfg.get("p").sources:
        s.enabled = False
    res = validate_pipeline(world.brain, load_config(world.brain), cfg.get("p"), editing="p")
    [issue] = [i for i in res.errors if i.code == "all-sources-off"]
    assert issue.message == "Turn a source on, or pause the pipeline." and issue.field == "sources"


def test_one_source_on_is_enough(world):
    cfg = load_config(world.brain)
    cfg.get("p").sources[0].enabled = False
    assert validate_pipeline(world.brain, load_config(world.brain), cfg.get("p"), editing="p").ok


def test_saving_does_not_call_jira_for_an_off_jira_source(world):
    cfg = load_config(world.brain)
    cfg.get("p").sources[1].enabled = False
    cfg.get("p").sources[1].options["jql"] = "project = MP"            # a changed query would be checked remotely if the source were on
    world.fake.requests.clear()
    res = validate_pipeline(world.brain, load_config(world.brain), cfg.get("p"), editing="p", remote=True)
    assert res.ok and world.fake.requests == []


def test_mcp_status_shows_an_off_source_as_off_and_not_as_a_problem(world):
    import asyncio
    import json

    from mcp.client import Client
    from mdpipeline.mcp_server import build_server

    set_enabled(world.brain, "docs", False)
    sync_pipeline(world.brain, "p", trigger="cli")

    async def call():
        async with Client(build_server(world.brain)) as client:
            result = await client.call_tool("sync_status", {})
            return json.loads(result.content[0].text)

    [p] = asyncio.run(call())["pipelines"]
    assert p["status"] == "ok" and p["sources_note"] == "1 of 2 sources off"
    assert [(s["label"], s["enabled"]) for s in p["sources"]] == [("docs", False), ("md-jira", True)]
