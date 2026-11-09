"""MD-212 (AC7, AC8): "background sync isn't running" is a state the product can see. The background process leaves a heartbeat each tick; background is
NOT running when it is on and announced, no process holds the lock, and the last heartbeat is older than the grace (or there is none).
The status data (MCP sync_status, CLI, app) always carries last_success_at, and when background is down also stale / stale_reason / message_for_user."""
from datetime import datetime, timezone

import pytest

from mdpipeline import background, scheduler
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.schedulestore import SchedulerStore
from mdpipeline.status import brain_status

NOW = 1_800_000_000.0
RESTART = "mdpipeline schedule background restart"


def set_state(enabled=True, announced=True, last_tick_at=None):
    with SchedulerStore().update() as data:
        data["background"].update({"enabled": enabled, "announced": announced})
        if last_tick_at is None:
            data["background"].pop("last_tick_at", None)
        else:
            data["background"]["last_tick_at"] = last_tick_at


@pytest.fixture
def dead(monkeypatch):
    monkeypatch.setattr(background, "is_running", lambda: False)


# --- the heartbeat ---------------------------------------------------------------------------------------------------------------------
def test_each_tick_of_the_background_process_leaves_a_heartbeat(app_data, monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    background.run(max_ticks=1, sleep=lambda s: None, interval=1, how="manual")
    assert abs(SchedulerStore().read()["background"]["last_tick_at"] - __import__("time").time()) < 5


# --- not_running -----------------------------------------------------------------------------------------------------------------------
def test_off_is_never_not_running(app_data, dead):
    set_state(enabled=False)
    assert background.not_running(NOW) is False


def test_before_the_announcement_is_never_not_running(app_data, dead):
    set_state(announced=False)
    assert background.not_running(NOW) is False


def test_a_running_process_is_not_not_running(app_data, monkeypatch):
    set_state(last_tick_at=NOW - 86400)
    monkeypatch.setattr(background, "is_running", lambda: True)
    assert background.not_running(NOW) is False


def test_a_recent_heartbeat_is_a_restart_in_progress_not_a_stop(app_data, dead):
    set_state(last_tick_at=NOW - background.NOT_RUNNING_GRACE_SECONDS + 5)
    assert background.not_running(NOW) is False


def test_an_old_heartbeat_with_no_process_is_not_running(app_data, dead):
    set_state(last_tick_at=NOW - background.NOT_RUNNING_GRACE_SECONDS - 5)
    assert background.not_running(NOW) is True


def test_no_heartbeat_at_all_with_no_process_is_not_running(app_data, dead):
    set_state(last_tick_at=None)
    assert background.not_running(NOW) is True


def test_the_grace_is_far_below_24_hours():
    assert 5 * 60 <= background.NOT_RUNNING_GRACE_SECONDS <= 3600


# --- status data -------------------------------------------------------------------------------------------------------------------------
def synced(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    sync_pipeline(brain, "p")


def test_status_always_carries_last_success_at_in_utc_iso(brain, add_pipeline, make_source, app_data):
    synced(brain, add_pipeline, make_source)
    data = brain_status(brain)
    at = data["pipelines"][0]["last_success_at"]
    assert datetime.fromisoformat(at).utcoffset() == timezone.utc.utcoffset(None) and at.endswith("Z") or at.endswith("+00:00")
    assert data["last_success_at"] == at


def test_status_with_nothing_synced_has_a_null_last_success_at(brain, add_pipeline, make_source, app_data):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    data = brain_status(brain)
    assert data["last_success_at"] is None and data["pipelines"][0]["last_success_at"] is None


def test_status_is_not_stale_while_background_runs_and_has_no_message(brain, add_pipeline, make_source, app_data, monkeypatch):
    synced(brain, add_pipeline, make_source)
    set_state(last_tick_at=None)
    monkeypatch.setattr(background, "is_running", lambda: True)
    data = brain_status(brain)
    assert data["stale"] is False and "message_for_user" not in data and "stale_reason" not in data


def test_status_is_stale_when_background_is_down_with_the_reason_and_a_sentence_to_quote(brain, add_pipeline, make_source, app_data, dead):
    synced(brain, add_pipeline, make_source)
    set_state(last_tick_at=None)
    data = brain_status(brain)
    assert data["stale"] is True and data["stale_reason"] == "background_not_running"
    human = datetime.fromisoformat(data["last_success_at"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")       # marketing: a human time, not raw ISO
    assert data["message_for_user"] == ("Markdown Pipeline background sync isn't running, so these files may be out of date. "
                                        f"Last update: {human}. Ask the user to run `{RESTART}` or open the app.")


def test_stale_with_nothing_synced_says_so_instead_of_last_update_never(brain, add_pipeline, make_source, app_data, dead):
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "1"}))]))
    set_state(last_tick_at=None)
    data = brain_status(brain)
    assert data["message_for_user"] == ("Markdown Pipeline background sync isn't running, and nothing has synced yet. "
                                        f"Ask the user to run `{RESTART}` or open the app.")


# --- the MCP tool --------------------------------------------------------------------------------------------------------------------------
def test_the_sync_status_tool_tells_the_agent_to_quote_message_for_user_as_is_and_returns_it_when_stale(populated_mcp):
    from test_mcp_server import session

    async def fn(c):
        tool = next(t for t in (await c.list_tools()).tools if t.name == "sync_status")
        import json
        result = await c.call_tool("sync_status", {})
        return tool.description, json.loads(result.content[0].text)

    description, data = session(populated_mcp, fn)
    assert "message_for_user" in description and "as is" in description and "last_success_at" in description
    assert data["stale"] is True and RESTART in data["message_for_user"]


@pytest.fixture
def populated_mcp(brain, add_pipeline, make_source, app_data, dead):
    synced(brain, add_pipeline, make_source)
    set_state(last_tick_at=None)
    return brain
