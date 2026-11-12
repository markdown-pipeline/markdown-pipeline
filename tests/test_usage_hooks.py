"""MD-116 (FR-S2 AC1, AC3): what the sync engine reports. Counts, buckets and enums only: nothing the user wrote can leave."""
from __future__ import annotations

import json

import pytest

from mdpipeline import usage, usage_hooks
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import cancel_pending, sync_pipeline
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import sanitize

CANARY_TITLE = "ACME-secret-roadmap"
CANARY_PATH = "private-client-folder"


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued(*names: str) -> list[dict]:
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


# --- pure mapping ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n,expected", [(0, "0"), (1, "1-10"), (10, "1-10"), (11, "11-100"), (100, "11-100"), (101, "101-1000"),
                                        (1000, "101-1000"), (1001, "1000+"), (10**6, "1000+")])
def test_counts_become_buckets(n, expected):
    assert usage_hooks.bucket(n) == expected


@pytest.mark.parametrize("trigger,expected", [("schedule", "schedule"), ("cli", "manual"), ("ui", "manual"), ("mcp", "manual"), ("x", "manual")])
def test_triggers_become_schedule_or_manual(trigger, expected):
    assert usage_hooks.trigger_class(trigger) == expected


def test_source_type_is_folder_jira_or_other_for_a_mix():
    jira = Source("", "j", type="jira", options={"site": "https://a.atlassian.net", "jql": "project = X"})
    assert usage_hooks.source_type_of(Pipeline.new("p", ["/a"])) == "folder"
    assert usage_hooks.source_type_of(Pipeline("p", [jira])) == "jira"
    assert usage_hooks.source_type_of(Pipeline("p", [Source("/a", "a"), jira])) == "other"


@pytest.mark.parametrize("category,expected", [
    ("auth", "auth"), ("vault-locked", "auth"), ("rate-limited", "rate_limit"), ("network", "network"), ("protected-branch", "target_write"),
    ("head-moved", "target_write"), ("concurrent-change", "target_write"), ("credential-conflict", "validation"), ("", "unknown"),
    ("something we never heard of", "unknown")])
def test_failure_categories_become_error_classes(category, expected):
    assert usage_hooks.error_class_of(category) == expected


@pytest.mark.parametrize("jql,fields,clauses,order_by", [
    ('project = ACME AND status in ("Open","Done") ORDER BY updated DESC', ["project", "status"], 2, True),
    ("updated >= -30d", ["updated"], 1, False),
    ('labels = "secret-label" and assignee = currentUser() and priority > Low', ["assignee", "labels", "priority"], 3, False),
    ('"Customer Name" ~ "ACME" AND type = Bug', ["custom", "issuetype"], 2, False),
    ("", [], 0, False)])
def test_a_query_is_reduced_to_field_names_a_clause_count_and_whether_it_sorts(jql, fields, clauses, order_by):
    profile = usage_hooks.jql_profile(jql)
    assert profile == {"fields_used": fields, "clause_count": clauses, "uses_order_by": order_by}


def test_the_query_profile_never_contains_any_value_from_the_query():
    profile = usage_hooks.jql_profile('project = ACME-SECRET AND labels = "secret-label" AND summary ~ "roadmap"')
    assert "ACME-SECRET" not in json.dumps(profile) and "secret-label" not in json.dumps(profile) and "roadmap" not in json.dumps(profile)


# --- the sync engine ---------------------------------------------------------------------------------------------------
@pytest.fixture
def folder_pipeline(brain, add_pipeline, make_source):
    src = make_source(CANARY_PATH, {f"{CANARY_TITLE}-{i}.md": f"# {CANARY_TITLE}\nbody {i}" for i in range(3)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    return src


def test_a_successful_sync_reports_sync_completed_with_buckets_and_the_trigger(brain, folder_pipeline, sharing):
    sync_pipeline(brain, "p", trigger="ui")
    (e,) = queued("sync_completed")
    p = e["properties"]
    assert p["trigger"] == "manual" and p["source_type"] == "folder" and p["changed_bucket"] == "1-10" and p["deleted_bucket"] == "0"
    assert isinstance(p["duration_s"], (int, float))


def test_the_first_file_reports_first_sync_completed_once_with_the_time_since_the_first_open(brain, folder_pipeline, sharing):
    UsageStore().touch_first_open()
    sync_pipeline(brain, "p", trigger="cli")
    sync_pipeline(brain, "p", trigger="cli")
    (e,) = queued("first_sync_completed")
    p = e["properties"]
    assert p["source_type"] == "folder" and p["files_bucket"] == "1-10" and isinstance(p["ms_since_first_open"], int)
    assert len(queued("sync_completed")) == 2


def test_a_sync_that_wrote_no_file_is_not_the_first_file(brain, add_pipeline, make_source, sharing):
    empty = make_source("empty", {})
    add_pipeline(Pipeline.new("e", [str(empty)]))
    sync_pipeline(brain, "e", trigger="cli")
    assert queued("first_sync_completed") == [] and UsageStore().ms_since_first_sync() is None


def test_the_first_sync_is_remembered_for_the_agent_read_clock(brain, folder_pipeline, sharing):
    sync_pipeline(brain, "p", trigger="cli")
    assert UsageStore().ms_since_first_sync() is not None


def test_a_failed_source_reports_sync_failed_with_an_error_class_and_no_text(brain, add_pipeline, tmp_path, sharing):
    add_pipeline(Pipeline.new("gone", [str(tmp_path / CANARY_PATH / "missing")]))
    sync_pipeline(brain, "gone", trigger="cli")
    (e,) = queued("sync_failed")
    assert e["properties"]["error_class"] in ("unknown", "validation", "network", "auth") and e["properties"]["source_type"] == "folder"
    assert CANARY_PATH not in json.dumps(e)


def test_a_blocked_sync_reports_guard_triggered_and_sync_failed_as_guard_blocked(brain, add_pipeline, make_source, sharing):
    src = make_source("big", {f"{i}.md": str(i) for i in range(10)})
    pipeline = Pipeline.new("g", [str(src)], guard_threshold_percent=10, guard_min_files=1)
    add_pipeline(pipeline)
    sync_pipeline(brain, "g", trigger="cli")
    for f in list(src.glob("*.md"))[:8]:
        f.unlink()
    res = sync_pipeline(brain, "g", trigger="cli")
    assert res.result == "blocked"
    assert [e["properties"]["cause"] for e in queued("guard_triggered")] == ["unknown"]
    assert queued("sync_failed")[-1]["properties"]["error_class"] == "guard_blocked"


def test_approving_and_cancelling_a_blocked_sync_report_guard_resolved(brain, add_pipeline, make_source, sharing):
    src = make_source("big", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("g", [str(src)], guard_threshold_percent=10, guard_min_files=1))
    sync_pipeline(brain, "g", trigger="cli")
    for f in list(src.glob("*.md"))[:8]:
        f.unlink()
    sync_pipeline(brain, "g", trigger="cli")
    cancel_pending(brain, "g", trigger="cli")
    sync_pipeline(brain, "g", trigger="cli")
    sync_pipeline(brain, "g", trigger="cli", approve=True)
    assert [e["properties"]["action"] for e in queued("guard_resolved")] == ["cancelled", "approved"]


@pytest.mark.parametrize("what", ["dry_run", "paused"])
def test_a_dry_run_and_a_paused_pipeline_report_nothing(brain, folder_pipeline, sharing, what):
    if what == "paused":
        cfg = load_config(brain)
        cfg.pipelines[0].paused = True
        save_config(brain, cfg)
        sync_pipeline(brain, "p", trigger="cli")
    else:
        sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert queued() == []


def test_without_consent_a_sync_stores_no_event_at_all(brain, folder_pipeline):
    sync_pipeline(brain, "p", trigger="cli")
    assert UsageStore().pending == 0 and UsageStore().ms_since_first_sync() is not None     # only the local clock is kept


def test_nothing_the_user_wrote_leaves_in_any_event_of_a_real_sync(brain, folder_pipeline, sharing):
    sync_pipeline(brain, "p", trigger="cli")
    sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    text = json.dumps(queued())
    for canary in (CANARY_TITLE, CANARY_PATH, str(brain.root), "body 0"):
        assert canary not in text


def test_every_queued_event_passes_the_allowlist_unchanged(brain, folder_pipeline, sharing):
    sync_pipeline(brain, "p", trigger="cli")
    for e in queued():
        own = {k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")}
        assert sanitize(e["event"], own) == own


def test_a_failure_in_the_usage_code_never_fails_or_changes_the_sync(brain, folder_pipeline, sharing, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("analytics exploded")

    monkeypatch.setattr(usage, "emit", boom)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and (brain.root / "raw").exists()


def test_a_sync_is_not_slowed_by_a_stuck_network(brain, folder_pipeline, sharing, monkeypatch):
    import threading
    import time

    release = threading.Event()
    monkeypatch.setattr(usage, "get_transport", lambda: (lambda batch: release.wait(10)))
    monkeypatch.setattr(usage, "FLUSH_DELAY", 0.01)
    t0 = time.monotonic()
    res = sync_pipeline(brain, "p", trigger="cli")
    elapsed = time.monotonic() - t0
    release.set()
    usage.wait_for_flush(5)
    assert res.result == "ok" and elapsed < 3


# --- MD-161 AC2: a locked vault is the person's to unlock, not a failure the reliability numbers should count ---------
def _locked_result():
    from types import SimpleNamespace

    return SimpleNamespace(dry_run=False, result="error", category="vault-locked", plan=None, trigger="schedule", duration_ms=5,
                           counts=lambda: {}, approved=False)


def test_a_locked_vault_is_not_reported_as_sync_failed(sharing):
    usage_hooks.sync_finished(_locked_result(), Pipeline.new("p", ["/a"]))
    assert queued("sync_failed") == []


def test_a_locked_vault_reports_the_needs_you_signal_instead(sharing):
    usage_hooks.sync_finished(_locked_result(), Pipeline.new("p", ["/a"]))
    (e,) = queued("notice_shown")
    assert e["properties"]["kind"] == "vault_locked"


# --- MD-231: the first sync of a person's OWN source, apart from the bundled sample ------------------------------------
def _sample_pipeline(add_pipeline):
    from mdpipeline import sample

    return add_pipeline(Pipeline(sample.NAME, [Source(str(sample.source_dir()), sample.LABEL)]))


def _jira_pipeline(add_pipeline, name="mine"):
    site = "https://acme.atlassian.net"
    return add_pipeline(Pipeline(name, [Source(site, "jira", type="jira", options={
        "site": site, "jql": "project = MD", "email_ref": "env:JIRA_EMAIL", "token_ref": "env:JIRA_TOKEN"})], guard_threshold_percent=100))


def test_sample_then_own_folder_gives_one_first_own_sync_and_a_flagged_first_sync(brain, folder_pipeline, add_pipeline, sharing):
    _sample_pipeline(add_pipeline)
    sync_pipeline(brain, "sample", trigger="ui")
    (first,) = queued("first_sync_completed")
    assert first["properties"]["is_sample"] is True and queued("first_own_sync_completed") == []
    sync_pipeline(brain, "p", trigger="cli")
    sync_pipeline(brain, "p", trigger="cli")
    (own,) = queued("first_own_sync_completed")
    assert own["properties"]["source_type"] == "folder" and own["properties"]["files_bucket"] == "1-10"
    assert isinstance(own["properties"]["duration_s"], (int, float))
    assert len(queued("first_sync_completed")) == 1


def test_sample_then_own_jira_gives_one_first_own_sync(brain, add_pipeline, sharing, monkeypatch):
    from fake_jira import FakeJira
    from mdpipeline import jira_api

    fake = FakeJira(site="acme", email="me@example.com", token="tok", display_name="Mia")
    fake.sleeps = []
    jira_api.set_transport(fake, sleep=fake.sleeps.append)
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    try:
        _sample_pipeline(add_pipeline)
        _jira_pipeline(add_pipeline)
        fake.add_issue("MD", "First issue")
        sync_pipeline(brain, "sample", trigger="ui")
        sync_pipeline(brain, "mine", trigger="cli")
        sync_pipeline(brain, "mine", trigger="cli")
    finally:
        jira_api.set_transport(None)
    (own,) = queued("first_own_sync_completed")
    assert own["properties"]["source_type"] == "jira"


def test_an_own_sync_that_wrote_no_file_is_not_the_first_own_sync_but_the_next_one_with_files_is(brain, add_pipeline, make_source, sharing):
    src = make_source("later", {})
    add_pipeline(Pipeline.new("e", [str(src)]))
    sync_pipeline(brain, "e", trigger="cli")
    assert queued("first_own_sync_completed") == []
    (src / "a.md").write_text("# a\nbody", encoding="utf-8")
    sync_pipeline(brain, "e", trigger="cli")
    assert len(queued("first_own_sync_completed")) == 1


def test_a_failed_own_sync_is_not_the_first_own_sync_and_a_failed_sample_is_flagged(brain, add_pipeline, tmp_path, sharing):
    add_pipeline(Pipeline.new("gone", [str(tmp_path / "missing")]))
    sync_pipeline(brain, "gone", trigger="cli")
    (own_fail,) = queued("sync_failed")
    assert queued("first_own_sync_completed") == [] and own_fail["properties"]["is_sample"] is False
    from mdpipeline import sample
    import shutil

    _sample_pipeline(add_pipeline)
    shutil.rmtree(sample.source_dir())
    sync_pipeline(brain, "sample", trigger="ui")
    assert [e["properties"]["is_sample"] for e in queued("sync_failed")][-1] is True


def test_an_install_with_no_sample_sends_both_first_events_on_its_first_own_sync(brain, folder_pipeline, sharing):
    sync_pipeline(brain, "p", trigger="cli")
    (first,) = queued("first_sync_completed")
    (own,) = queued("first_own_sync_completed")
    assert first["properties"]["is_sample"] is False
    assert {k: v for k, v in own["properties"].items() if k in ("source_type", "files_bucket")} == \
           {k: v for k, v in first["properties"].items() if k in ("source_type", "files_bucket")}
    assert "ms_since_first_open" in own["properties"] or UsageStore().ms_since_first_open() is None


def test_the_sample_sync_is_flagged_on_sync_completed_and_an_own_one_is_not(brain, folder_pipeline, add_pipeline, sharing):
    _sample_pipeline(add_pipeline)
    sync_pipeline(brain, "sample", trigger="ui")
    sync_pipeline(brain, "p", trigger="ui")
    assert [e["properties"]["is_sample"] for e in queued("sync_completed")] == [True, False]


def test_a_user_pipeline_named_sample_is_not_the_sample(brain, add_pipeline, make_source, sharing):
    src = make_source("mine", {"a.md": "# a"})
    add_pipeline(Pipeline.new("sample", [str(src)]))
    sync_pipeline(brain, "sample", trigger="cli")
    (first,) = queued("first_sync_completed")
    assert first["properties"]["is_sample"] is False and len(queued("first_own_sync_completed")) == 1


def test_without_consent_the_new_event_queues_nothing(brain, folder_pipeline):
    UsageStore().set_consent("decline")
    sync_pipeline(brain, "p", trigger="cli")
    assert queued() == []


def test_pipeline_and_source_events_carry_is_sample(brain, add_pipeline, make_source, sharing):
    own = Pipeline.new("mine", [str(make_source("m", {"a.md": "x"}))])
    usage_hooks.pipeline_created(1, own)
    from mdpipeline import sample

    usage_hooks.pipeline_created(2, Pipeline(sample.NAME, [Source(str(sample.source_dir()), sample.LABEL)]))
    for name in ("pipeline_created", "source_added", "source_connected"):
        assert [e["properties"]["is_sample"] for e in queued(name)] == [False, True], name


# --- MD-231 AC10: the once-per-install stamp is set only when the event is actually queued -------------------------------
@pytest.mark.parametrize("choice", ["decline", None])
def test_not_sharing_sets_no_first_own_sync_stamp_then_share_sends_exactly_one(brain, folder_pipeline, choice):
    if choice:
        UsageStore().set_consent(choice)
    sync_pipeline(brain, "p", trigger="cli")
    assert queued() == [] and UsageStore()._read().get("first_own_sync_ts") is None
    UsageStore().set_consent("share")
    sync_pipeline(brain, "p", trigger="cli")
    assert len(queued("first_own_sync_completed")) == 1
    sync_pipeline(brain, "p", trigger="cli")
    assert len(queued("first_own_sync_completed")) == 1
