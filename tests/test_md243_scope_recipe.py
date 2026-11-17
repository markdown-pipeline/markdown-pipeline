"""MD-243: a recipe run is the app setting a built-in JQL, not a person narrowing scope; a person editing it afterwards is, and says which recipe."""
from __future__ import annotations

import json

import pytest

from mdpipeline import usage, usage_hooks
from mdpipeline.config import Pipeline, Source
from mdpipeline.usage import UsageStore

SITE = "https://issues.example.org"


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def scope_events() -> list[dict]:
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e["properties"] for e in entries if e["event"] == "source_scope_edited"]


def jira(jql, recipe=None):
    options = {"site": SITE, "jql": jql, "auth_mode": "public"}
    if recipe:
        options["recipe_id"] = recipe
    return Source("", "j", type="jira", options=options)


def test_creating_a_recipe_pipeline_sends_no_scope_edit(sharing):
    usage_hooks.pipeline_created(1, Pipeline("jira-hibernate-hhh", [jira("project = HHH", "jira-hibernate-hhh")]))
    assert scope_events() == []


def test_editing_a_recipe_pipelines_query_sends_one_scope_edit_with_the_recipe_id(sharing):
    old = Pipeline("r", [jira("project = HHH", "jira-hibernate-hhh")])
    new = Pipeline("r", [jira("project = HHH AND labels = x", "jira-hibernate-hhh")])
    usage_hooks.pipeline_updated(old, new)
    (event,) = scope_events()
    assert event["recipe_id"] == "jira-hibernate-hhh"


def test_a_persons_own_pipeline_scope_edit_has_recipe_id_none(sharing):
    usage_hooks.pipeline_created(1, Pipeline("p", [jira("project = ACME")]))
    (event,) = scope_events()
    assert event["recipe_id"] == "none"


def test_a_recipe_scope_edit_carries_no_query_text(sharing):
    old = Pipeline("r", [jira("project = HHH", "jira-hibernate-hhh")])
    new = Pipeline("r", [jira("project = HHH AND labels = secret-word", "jira-hibernate-hhh")])
    usage_hooks.pipeline_updated(old, new)
    assert "secret-word" not in json.dumps(scope_events()) and "HHH" not in json.dumps(scope_events())
