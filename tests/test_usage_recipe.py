"""MD-238 (EXP-1112-RC1 G2): recipe measurement. recipe_id on usage events, first_recipe_sync_completed, and a recipe is never the person's own first sync."""
from __future__ import annotations

import json

import pytest

from mdpipeline import usage, usage_hooks
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import sanitize

HHH = "jira-hibernate-hhh"


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued(*names: str) -> list[dict]:
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


SITE = "https://acme.atlassian.net"


def recipe_source(recipe_id: str = HHH, **extra) -> Source:
    """A source as `mdpipeline recipe` saves it (MD-237 AC3): Jira in public mode, with the recipe marker."""
    return Source(SITE, "jira", type="jira", options={"site": SITE, "jql": extra.pop("jql", "project = MD"), "auth_mode": "public", "recipe_id": recipe_id, **extra})


@pytest.fixture
def jira():
    from fake_jira import FakeJira
    from mdpipeline import jira_api

    fake = FakeJira(site="acme", email="me@example.com", token="tok", display_name="Mia")
    fake.public = True
    fake.sleeps = []
    jira_api.set_transport(fake, sleep=fake.sleeps.append)
    for i in range(3):
        fake.add_issue("MD", f"Issue {i}")
    yield fake
    jira_api.set_transport(None)


@pytest.fixture
def recipe_pipeline(brain, add_pipeline, jira):
    return add_pipeline(Pipeline("hhh", [recipe_source()], guard_threshold_percent=100))


@pytest.fixture
def own_pipeline(brain, add_pipeline, make_source):
    src = make_source("own", {"a.md": "# a\nbody"})
    return add_pipeline(Pipeline.new("own", [str(src)]))


# --- the allowlist ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["jira-ecosystem-frge", "jira-hibernate-hhh", "jira-sakaiproject-sak", "none"])
def test_recipe_id_accepts_only_the_built_in_ids_and_none(value):
    assert sanitize("sync_completed", {"recipe_id": value}) == {"recipe_id": value}


@pytest.mark.parametrize("value", ["https://hibernate.atlassian.net", "project = HHH", "mixed", "my-recipe", "", None, 3])
def test_recipe_id_drops_anything_else(value):
    assert "recipe_id" not in (sanitize("sync_completed", {"recipe_id": value}) or {})


# --- recipe_id on the listed events ----------------------------------------------------------------------------------------
def test_a_recipe_sync_carries_its_recipe_id_on_sync_and_first_sync_events(brain, recipe_pipeline, sharing):
    sync_pipeline(brain, "hhh", trigger="cli")
    assert [e["properties"]["recipe_id"] for e in queued("sync_completed", "first_sync_completed")] == [HHH, HHH]


def test_an_own_sync_carries_recipe_id_none(brain, own_pipeline, sharing):
    sync_pipeline(brain, "own", trigger="cli")
    assert [e["properties"]["recipe_id"] for e in queued("sync_completed", "first_sync_completed")] == ["none", "none"]


def test_a_failed_recipe_sync_carries_its_recipe_id(brain, recipe_pipeline, jira, sharing):
    jira.public = False                                      # the site stops answering anonymous requests: the sync fails
    sync_pipeline(brain, "hhh", trigger="cli")
    (e,) = queued("sync_failed")
    assert e["properties"]["recipe_id"] == HHH


def test_creating_a_recipe_pipeline_carries_its_recipe_id_on_the_creation_events(brain, recipe_pipeline, sharing):
    usage_hooks.pipeline_created(1, recipe_pipeline)
    got = queued("pipeline_created", "source_added", "source_connected")
    assert sorted(e["event"] for e in got) == ["pipeline_created", "source_added", "source_connected"]
    assert {e["properties"]["recipe_id"] for e in got} == {HHH}


def test_creating_an_own_pipeline_carries_recipe_id_none(brain, own_pipeline, sharing):
    usage_hooks.pipeline_created(1, own_pipeline)
    assert {e["properties"]["recipe_id"] for e in queued("pipeline_created", "source_added", "source_connected")} == {"none"}


def test_an_unknown_marker_on_a_source_is_sent_as_none_never_as_text(brain, add_pipeline, jira, sharing):
    add_pipeline(Pipeline("odd", [recipe_source("https://secret.atlassian.net")], guard_threshold_percent=100))
    sync_pipeline(brain, "odd", trigger="cli")
    assert {e["properties"]["recipe_id"] for e in queued("sync_completed")} == {"none"}
    assert "secret" not in json.dumps(queued())


# --- first_own_sync_completed never fires for a recipe ---------------------------------------------------------------------
def test_a_recipe_sync_is_not_the_first_own_sync(brain, recipe_pipeline, sharing):
    sync_pipeline(brain, "hhh", trigger="cli")
    assert queued("first_own_sync_completed") == []


def test_an_own_sync_after_a_recipe_sync_is_still_the_first_own_sync(brain, recipe_pipeline, own_pipeline, sharing):
    sync_pipeline(brain, "hhh", trigger="cli")
    sync_pipeline(brain, "own", trigger="cli")
    (e,) = queued("first_own_sync_completed")
    assert e["properties"]["source_type"] == "folder"


# --- first_recipe_sync_completed -------------------------------------------------------------------------------------------
def test_the_first_recipe_sync_with_files_sends_first_recipe_sync_completed_with_its_properties(brain, recipe_pipeline, sharing):
    UsageStore().touch_first_open()
    sync_pipeline(brain, "hhh", trigger="cli")
    (e,) = queued("first_recipe_sync_completed")
    p = e["properties"]
    assert p["recipe_id"] == HHH and p["files_bucket"] == "1-10" and isinstance(p["duration_s"], (int, float)) and isinstance(p["ms_since_first_open"], int)
    assert set(p) >= {"recipe_id", "files_bucket", "duration_s", "ms_since_first_open"}


def test_it_is_sent_once_per_install(brain, recipe_pipeline, sharing):
    sync_pipeline(brain, "hhh", trigger="cli")
    sync_pipeline(brain, "hhh", trigger="cli")
    assert len(queued("first_recipe_sync_completed")) == 1


def test_a_second_recipe_does_not_send_it_again(brain, recipe_pipeline, add_pipeline, sharing):
    add_pipeline(Pipeline("sak", [recipe_source("jira-sakaiproject-sak")], guard_threshold_percent=100))
    sync_pipeline(brain, "hhh", trigger="cli")
    sync_pipeline(brain, "sak", trigger="cli")
    assert len(queued("first_recipe_sync_completed")) == 1


def test_a_recipe_sync_that_wrote_no_file_is_not_the_first_recipe_sync(brain, add_pipeline, jira, sharing):
    add_pipeline(Pipeline("hhh", [recipe_source(jql="project = ZZZ")], guard_threshold_percent=100))
    sync_pipeline(brain, "hhh", trigger="cli")
    assert len(queued("sync_completed")) == 1 and queued("first_recipe_sync_completed") == []      # it ran fine, it just wrote nothing


def test_an_own_sync_never_sends_first_recipe_sync_completed(brain, own_pipeline, sharing):
    sync_pipeline(brain, "own", trigger="cli")
    assert queued("first_recipe_sync_completed") == []


def test_without_consent_no_recipe_event_is_sent_and_a_later_yes_still_sends_exactly_one(brain, recipe_pipeline):
    sync_pipeline(brain, "hhh", trigger="cli")
    assert queued("first_recipe_sync_completed") == []
    UsageStore().set_consent("share")
    sync_pipeline(brain, "hhh", trigger="cli")
    assert len(queued("first_recipe_sync_completed")) == 1


def test_no_host_project_or_jql_is_in_any_serialized_event(brain, recipe_pipeline, sharing):
    sync_pipeline(brain, "hhh", trigger="cli")
    usage_hooks.pipeline_created(1, recipe_pipeline)
    blob = json.dumps(queued())
    for banned in ("acme", "atlassian", "project = MD", "MD-1", "Issue 0"):
        assert banned not in blob


# --- first_agent_read carries the recipe_id of what the first read touched (AC3) ---------------------------------------------
class TestFirstAgentReadRecipeId:
    @pytest.fixture
    def two(self, brain, brain_dir, add_pipeline):
        """Two pipelines with one file each: ``hhh`` is a recipe (marked source), ``own`` is not. Files carry the provenance the app writes."""
        from conftest import write

        add_pipeline(Pipeline("hhh", [recipe_source()]))
        add_pipeline(Pipeline("sak", [recipe_source("jira-sakaiproject-sak")]))
        add_pipeline(Pipeline("own", [Source("/x", "x")]))
        for name in ("hhh", "sak", "own"):
            write(brain_dir / f"raw/{name}/a.md", f"---\nprovenance:\n  pipeline: {name}\n  source_path: a.md\n---\n# {name}\nneedle {name}\n")
        UsageStore().set_consent("share")
        return brain

    @staticmethod
    def call(brain, *calls):
        import asyncio

        from mcp.client import Client

        from mdpipeline.mcp_server import build_server

        async def go():
            async with Client(build_server(brain)) as client:
                return [await client.call_tool(name, args) for name, args in calls]

        return asyncio.run(go())

    @staticmethod
    def first_read() -> dict:
        (e,) = queued("first_agent_read")
        return e["properties"]

    def test_reading_a_recipe_file_carries_that_recipe(self, two):
        self.call(two, ("read", {"path": "raw/hhh/a.md"}))
        assert self.first_read()["recipe_id"] == HHH

    def test_reading_a_non_recipe_file_carries_none(self, two):
        self.call(two, ("read", {"path": "raw/own/a.md"}))
        assert self.first_read()["recipe_id"] == "none"

    def test_a_search_whose_results_are_one_recipe_carries_it(self, two):
        self.call(two, ("search", {"query": "needle hhh", "path_prefix": "raw/hhh"}))
        assert self.first_read()["recipe_id"] == HHH

    def test_a_search_with_no_results_carries_none(self, two):
        self.call(two, ("search", {"query": "nothingmatchesthis"}))
        assert self.first_read()["recipe_id"] == "none"

    def test_a_search_across_two_recipes_is_mixed(self, two):
        self.call(two, ("search", {"query": "needle"}))
        assert self.first_read()["recipe_id"] == "mixed"

    def test_a_second_read_of_another_kind_does_not_change_what_the_first_read_was(self, two):
        self.call(two, ("search", {"query": "needle", "path_prefix": "raw/own"}), ("list", {}))
        assert self.first_read()["recipe_id"] == "none"                     # the first read touched only the own pipeline

    def test_a_list_of_one_recipe_folder_carries_it(self, two):
        self.call(two, ("list", {"path_prefix": "raw/sak"}))
        assert self.first_read()["recipe_id"] == "jira-sakaiproject-sak"

    def test_recent_changes_with_a_pipeline_argument_carries_that_pipelines_recipe(self, two):
        self.call(two, ("recent_changes", {"pipeline": "hhh"}))
        assert self.first_read()["recipe_id"] == HHH

    def test_only_the_first_read_is_tagged_and_it_is_sent_once(self, two):
        self.call(two, ("read", {"path": "raw/hhh/a.md"}), ("read", {"path": "raw/own/a.md"}))
        assert len(queued("first_agent_read")) == 1 and self.first_read()["recipe_id"] == HHH

    def test_a_search_across_several_non_recipe_pipelines_is_none_not_mixed(self, brain, brain_dir, add_pipeline):
        from conftest import write

        for name in ("own1", "own2"):
            add_pipeline(Pipeline(name, [Source(f"/{name}", name)]))
            write(brain_dir / f"raw/{name}/a.md", f"---\nprovenance:\n  pipeline: {name}\n  source_path: a.md\n---\n# {name}\nneedle\n")
        UsageStore().set_consent("share")
        self.call(brain, ("search", {"query": "needle"}))
        assert self.first_read()["recipe_id"] == "none"

    def test_a_failed_read_still_counts_and_is_none(self, two):
        self.call(two, ("read", {"path": "raw/hhh/missing.md"}))
        assert self.first_read()["recipe_id"] == "none"
