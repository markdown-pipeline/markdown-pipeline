"""MD-116 (FR-S2 AC1): configuration events from the CLI and the UI: pipeline created, sources added/removed/toggled, scope edited, target."""
from __future__ import annotations

import json

import pytest

from mdpipeline import usage, usage_hooks
from mdpipeline.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, Pipeline, Source, load_config
from mdpipeline.service import create_pipeline, update_pipeline
from mdpipeline.usage import UsageStore

SITE = "https://acme-secret.atlassian.net"


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued(*names: str) -> list[dict]:
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def props(*names: str) -> list[dict]:
    return [{k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")} for e in queued(*names)]


def jira(label="j", jql="project = ACME-SECRET"):
    return Source("", label, type="jira", options={"site": SITE, "jql": jql})


# --- creation --------------------------------------------------------------------------------------------------------
def test_creating_a_pipeline_reports_it_its_sources_and_its_target(brain, make_source, sharing):
    a, b = make_source("a", {"1.md": "1"}), make_source("b", {"2.md": "2"})
    create_pipeline(brain, Pipeline.new("p", [str(a), str(b)]))
    assert props("pipeline_created") == [{"n_pipelines": 1}]
    assert props("source_added") == [{"source_type": "folder", "n_sources": 2}] * 2
    assert props("source_connected") == [{"source_type": "folder"}] * 2
    assert props("target_connected") == [{"target_type": "folder"}]


def test_the_pipeline_count_is_the_total_after_creation(brain, make_source, sharing):
    create_pipeline(brain, Pipeline.new("p1", [str(make_source("a", {"1.md": "1"}))]))
    create_pipeline(brain, Pipeline.new("p2", [str(make_source("b", {"1.md": "1"}))]))
    assert [p["n_pipelines"] for p in props("pipeline_created")] == [1, 2]


def test_a_pipeline_that_fails_validation_reports_nothing(brain, make_source, sharing):
    from mdpipeline.service import ValidationFailed

    src = make_source("a", {"1.md": "1"})
    create_pipeline(brain, Pipeline.new("p", [str(src)]))
    before = len(queued())
    with pytest.raises(ValidationFailed):
        create_pipeline(brain, Pipeline.new("p", [str(src)]))          # the name is taken: refused, nothing saved
    assert len(queued()) == before


def test_without_consent_nothing_is_stored(brain, make_source):
    create_pipeline(brain, Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    assert UsageStore().pending == 0


def test_a_github_destination_is_reported_as_github():
    p = Pipeline("p", [Source("/a", "a")], destination={"type": "github", "repo": "acme-secret/brain", "branch": "main", "credential": "vault:x"})
    assert usage_hooks.target_type_of(p) == "github"
    assert usage_hooks.target_type_of(Pipeline("p", [Source("/a", "a")])) == "folder"
    assert usage_hooks.target_type_of(Pipeline("p", [Source("/a", "a")], destination={"type": "directory", "path": "/x"})) == "folder"


def test_a_jira_query_at_creation_is_reported_as_a_scope_with_field_names_only(sharing):
    usage_hooks.pipeline_created(1, Pipeline("p", [jira(jql='project = ACME-SECRET AND status = "Open" ORDER BY updated')]))
    (scope,) = props("source_scope_edited")
    assert scope == {"source_type": "jira", "kind": "jql", "fields_used": ["project", "status"], "clause_count": 2, "uses_order_by": True}
    assert "ACME-SECRET" not in json.dumps(queued()) and SITE not in json.dumps(queued())


def test_default_folder_rules_are_not_a_scope_edit_but_custom_ones_are(sharing):
    usage_hooks.pipeline_created(1, Pipeline.new("p", ["/a"]))
    assert queued("source_scope_edited") == []
    usage_hooks.pipeline_created(2, Pipeline.new("q", ["/a"], include=["docs/**/*.md"], exclude=DEFAULT_EXCLUDE + ["drafts/**"]))
    (scope,) = props("source_scope_edited")
    assert scope["kind"] == "glob" and scope["pattern_count"] == 1 + len(DEFAULT_EXCLUDE) + 1 and scope["source_type"] == "folder"


# --- editing ---------------------------------------------------------------------------------------------------------
def test_adding_a_source_reports_it_with_the_new_total(brain, make_source, sharing):
    a, b = make_source("a", {"1.md": "1"}), make_source("b", {"2.md": "2"})
    create_pipeline(brain, Pipeline.new("p", [str(a)]))
    queue_before = len(queued())
    edited = Pipeline.new("p", [str(a), str(b)])
    update_pipeline(brain, "p", edited)
    new = queued()[queue_before:]
    assert [e["event"] for e in new] == ["source_added", "source_connected"]
    assert new[0]["properties"]["n_sources"] == 2 and new[0]["properties"]["source_type"] == "folder"


def test_removing_a_source_reports_it_without_saying_which(brain, make_source, sharing):
    a, b = make_source("a", {"1.md": "1"}), make_source("b", {"2.md": "2"})
    create_pipeline(brain, Pipeline.new("p", [str(a), str(b)]))
    before = len(queued())
    update_pipeline(brain, "p", Pipeline.new("p", [str(a)]), after_validation=lambda: None)
    new = queued()[before:]
    assert [e["event"] for e in new] == ["source_removed"]
    assert json.dumps(new).count(str(b)) == 0


def test_switching_a_source_off_or_on_reports_the_new_state(brain, make_source, sharing):
    a = make_source("a", {"1.md": "1"})
    b = make_source("b", {"2.md": "2"})
    create_pipeline(brain, Pipeline.new("p", [str(a), str(b)]))
    before = len(queued())
    off = Pipeline.new("p", [str(a), str(b)])
    off.sources[1].enabled = False
    update_pipeline(brain, "p", off)
    on = Pipeline.new("p", [str(a), str(b)])
    update_pipeline(brain, "p", on)
    assert [p["state"] for p in props("source_toggled")] == ["off", "on"]
    assert len(queued()) > before


def test_changing_a_jira_query_reports_a_scope_edit_and_a_changed_target_reports_the_target(sharing):
    old = Pipeline("p", [jira(jql="project = A")])
    new = Pipeline("p", [jira(jql="project = A AND labels = secret")], destination={"type": "github", "repo": "o/r", "branch": "main", "credential": "vault:x"})
    usage_hooks.pipeline_updated(old, new)
    assert props("source_scope_edited") == [{"source_type": "jira", "kind": "jql", "fields_used": ["labels", "project"], "clause_count": 2,
                                             "uses_order_by": False}]
    assert props("target_connected") == [{"target_type": "github"}]


def test_an_edit_that_changes_nothing_reports_nothing(brain, make_source, sharing):
    a = make_source("a", {"1.md": "1"})
    create_pipeline(brain, Pipeline.new("p", [str(a)]))
    before = len(queued())
    update_pipeline(brain, "p", Pipeline.new("p", [str(a)]))
    assert len(queued()) == before


def test_a_failure_in_the_usage_code_never_fails_saving_a_pipeline(brain, make_source, sharing, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("analytics exploded")

    monkeypatch.setattr(usage, "emit", boom)
    create_pipeline(brain, Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    assert load_config(brain).get("p") is not None
