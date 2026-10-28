"""MD-162 AC3 (server side): the page's cheap poll (/api/version) changes when the background starts or stops and when a "needs you" notice
appears or clears, so open screens refresh by themselves. These were invisible to it before: it only watched the brain's own files."""
import time

import pytest

from bg_helper import FakeBackgroundProcess
from mdpipeline import notices
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.schedulestore import SchedulerStore
from uihelper import running_ui


@pytest.fixture
def one(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "acme", trigger="cli")
    return brain


def version(ui):
    return ui.get("/api/version")[1]["version"]


def test_the_version_changes_when_the_background_process_starts_and_when_it_stops(one):
    proc = FakeBackgroundProcess()
    with running_ui(one) as ui:
        before = version(ui)
        proc.start()
        time.sleep(0.3)
        started = version(ui)
        proc.stop()
        stopped = version(ui)
    assert started != before and stopped != started and stopped == before


def test_the_version_changes_when_a_needs_you_notice_appears_and_clears(one, monkeypatch):
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)
    with running_ui(one) as ui:
        before = version(ui)
        for _ in range(2):
            ActivityLog(one).append({"type": "run", "pipeline": "acme", "trigger": "schedule", "result": "error", "counts": {}, "conflicts": [],
                                     "errors": [{"source": "a", "error": "boom", "category": "network"}]})
        notices.check(one)
        raised = version(ui)
        with SchedulerStore().update() as data:
            data["runtime"][str(one.root)]["notices"] = {}
        cleared = version(ui)
    assert raised != before and cleared != raised


def test_the_state_reports_whether_the_background_runs_so_the_page_can_tell(one):
    proc = FakeBackgroundProcess()
    with running_ui(one) as ui:
        assert ui.get("/api/state")[1]["background"]["running"] is False
        proc.start()
        time.sleep(0.3)
        assert ui.get("/api/state")[1]["background"]["running"] is True
        proc.stop()
