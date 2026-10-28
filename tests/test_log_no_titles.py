"""MD-71: the activity log identifies Jira-sourced files by issue key and counts, never by title or title-derived file name."""
import json

import pytest

from fake_jira import adf_doc, adf_p
from mdpipeline.config import Pipeline, load_config, save_config
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.removal import remove_pipeline
from test_jira_sync import day, fake, jira_source, raw_files  # noqa: F401

MARKER = "ZXQTITLEMARKER"


@pytest.fixture
def world(brain, add_pipeline, fake):
    add_pipeline(Pipeline("p", [jira_source("project = MD")], name_policy="original", guard_threshold_percent=100))
    return type("W", (), {"brain": brain, "fake": fake, "sync": staticmethod(lambda **kw: sync_pipeline(brain, "p", trigger="cli", **kw)),
                          "log_text": staticmethod(lambda: brain.config_dir.joinpath("log.jsonl").read_text())})


def log_files(w):
    return [f for run in ActivityLog(w.brain).read(types={"run"}) for f in run.get("files", [])]


def test_create_update_rename_and_delete_leave_no_title_in_the_log(world):
    fake = world.fake
    fake.add_issue("MD", f"{MARKER} first", created=day(2026, 10, 1))
    fake.add_issue("MD", f"{MARKER} second", created=day(2026, 10, 2))
    keep = fake.add_issue("MD", f"{MARKER} third", created=day(2026, 10, 3))
    assert world.sync().result == "ok" and any(MARKER in f for f in raw_files(world.brain))        # the file names DO carry titles
    fake.update_issue("MD-1", description=adf_doc(adf_p("new body")))
    assert world.sync().counts()["updated"] == 1
    fake.update_issue("MD-2", summary=f"{MARKER} renamed")
    res = world.sync()
    assert res.counts()["renamed"] == 1
    fake.delete_issue("MD-3")
    assert world.sync().counts()["deleted"] == 1
    assert MARKER not in world.log_text()


def test_the_log_still_says_what_changed_by_key_action_and_counts(world):
    world.fake.add_issue("MD", f"{MARKER} one", created=day(2026, 10, 1))
    world.fake.add_issue("MD", f"{MARKER} two", created=day(2026, 10, 2))
    world.sync()
    world.fake.update_issue("MD-1", summary=f"{MARKER} retitled")
    world.sync()
    world.fake.delete_issue("MD-2")
    world.sync()
    entries = [(f["action"], f.get("key")) for f in log_files(world)]
    assert entries == [("create", "MD-1"), ("create", "MD-2"), ("rename", "MD-1"), ("delete", "MD-2")]
    runs = ActivityLog(world.brain).read(types={"run"})
    assert [r["counts"]["created"] for r in runs] == [2, 0, 0] and runs[1]["counts"]["renamed"] == 1 and runs[2]["counts"]["deleted"] == 1


def test_a_conflict_and_a_force_log_the_key_not_the_path(world):
    world.fake.add_issue("MD", f"{MARKER} edited by me", created=day(2026, 10, 1))
    world.sync()
    [path] = [p for p in world.brain.root.joinpath("raw").rglob("*.md")]
    path.write_text(path.read_text() + "\nmy own edit\n")
    world.fake.update_issue("MD-1", description=adf_doc(adf_p("changed in Jira")))
    res = world.sync()
    assert res.result == "conflicts" and MARKER not in world.log_text()
    forced = sync_pipeline(world.brain, "p", trigger="cli", force=True)
    assert forced.result == "ok" and MARKER not in world.log_text()
    assert any(e.get("action") == "force" and "MD-1" in json.dumps(e) for e in ActivityLog(world.brain).read(types={"event"}))


def test_removing_a_pipeline_logs_keys_not_paths(world):
    world.fake.add_issue("MD", f"{MARKER} stays behind", created=day(2026, 10, 1))
    world.sync()
    remove_pipeline(world.brain, "p", trigger="cli")
    assert MARKER not in world.log_text()


def test_an_issue_that_cannot_be_converted_is_named_by_its_key_in_the_log(world):
    world.fake.add_issue("MD", f"{MARKER} broken", description={"type": "doc", "version": 1, "content": [{"type": "bogus-node-that-fails"}]}, created=day(2026, 10, 1))
    world.sync()
    assert MARKER not in world.log_text()


def test_the_activity_screen_still_lists_what_a_jira_run_did_by_key(world):
    from mdpipeline.uiactivity import activity_view

    world.fake.add_issue("MD", f"{MARKER} visible", created=day(2026, 10, 1))
    world.sync()
    details = activity_view(world.brain)["history"][0]["details"]
    assert details and "MD-1" in json.dumps(details) and MARKER not in json.dumps(activity_view(world.brain)["history"])


def test_folder_sources_keep_their_paths_in_the_log(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("f", [str(make_source("docs", {"my notes.md": "x"}))]))
    sync_pipeline(brain, "f", trigger="cli")
    assert any("my notes" in (f.get("target") or "") for f in log_files(type("W", (), {"brain": brain})))


def test_the_readme_states_the_rule():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()
    assert "## What the activity log contains" in text and "never by issue title" in text
