"""MD-240: a pipeline shown as scheduled must run, or `schedule` / `status` must say plainly that it won't (and the one command), or that it is overdue."""
from __future__ import annotations

import json
import time

import pytest

from mdpipeline import autostart
from mdpipeline.cli import main
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.schedulestore import SchedulerStore
from mdpipeline.status import brain_status


@pytest.fixture
def synced(brain, add_pipeline, make_source, monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    return brain


def unregister(brain):
    with SchedulerStore().update() as data:
        data["brains"] = [b for b in data["brains"] if b != str(brain.root)]


def run(brain, *argv):
    return main(["--brain", str(brain.root), *argv])


def problem(brain, now=None):
    return brain_status(brain, now=now)["pipelines"][0]["schedule_problem"]


def test_a_brain_the_background_does_not_serve_is_reported_with_the_one_command(synced):
    unregister(synced)
    p = problem(synced)
    assert p["kind"] == "not_served" and "mdpipeline sync p" in p["message"]


def test_a_registered_brain_has_no_schedule_problem(synced):
    assert problem(synced) is None


def test_a_scheduled_pipeline_overdue_by_more_than_twice_its_interval_is_flagged(synced):
    p = problem(synced, now=time.time() + 2 * 3600 + 120)             # every 1h: 2h02m since the last sync
    assert p["kind"] == "overdue" and "overdue" in p["message"]


def test_not_yet_twice_the_interval_is_not_overdue(synced):
    assert problem(synced, now=time.time() + 2 * 3600 - 120) is None


def test_with_background_sync_turned_off_nothing_is_promised_so_nothing_is_flagged(synced):
    unregister(synced)
    with SchedulerStore().update() as data:
        data["background"]["enabled"] = False
    assert problem(synced) is None


def test_schedule_prints_the_problem_under_the_pipeline(synced, capsys):
    unregister(synced)
    run(synced, "schedule")
    out = capsys.readouterr().out
    assert "p: every 1h" in out and "mdpipeline sync p" in out


def test_status_prints_the_problem_and_json_carries_it(synced, capsys):
    unregister(synced)
    run(synced, "status")
    assert "mdpipeline sync p" in capsys.readouterr().out
    run(synced, "status", "--json")
    assert json.loads(capsys.readouterr().out)["pipelines"][0]["schedule_problem"]["kind"] == "not_served"


def test_a_sync_registers_the_brain_so_the_problem_goes_away(synced):
    unregister(synced)
    sync_pipeline(synced, "p", trigger="cli")
    assert problem(synced) is None
