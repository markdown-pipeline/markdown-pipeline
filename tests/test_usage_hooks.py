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
