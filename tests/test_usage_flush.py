"""MD-116 (FR-S2 AC1, FR-S1 AC4): events are sent in the background; the caller (sync, UI, CLI, MCP) never waits for the network."""
from __future__ import annotations

import threading
import time

import pytest

from mdpipeline import cli, usage
from mdpipeline.cli import main
from mdpipeline.usage import UsageStore


class Recorder:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def __call__(self, batch):
        self.batches.append(batch)

    @property
    def events(self):
        return [e for b in self.batches for e in b]


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


@pytest.fixture(autouse=True)
def quick_flush(monkeypatch):
    monkeypatch.setattr(usage, "FLUSH_DELAY", 0.05)


# --- in-process background flush (UI server, MCP server) -------------------------------------------------------------
def test_emit_sends_in_the_background_after_a_short_delay(sharing, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    assert usage.emit("app_opened", first_open=True) is True
    assert transport.events == []                          # not sent inline
    assert usage.wait_for_flush(5)
    assert [e["event"] for e in transport.events] == ["app_opened"]
    assert UsageStore().pending == 0


def test_emit_returns_at_once_even_when_the_network_is_stuck(sharing, monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def stuck(batch):
        started.set()
        release.wait(10)

    monkeypatch.setattr(usage, "get_transport", lambda: stuck)
    t0 = time.monotonic()
    usage.emit("app_opened")
    assert started.wait(5)                                 # the flush is now blocked inside the transport
    usage.emit("app_opened")                               # emitting more meanwhile is still instant
    assert time.monotonic() - t0 < 2
    release.set()
    assert usage.wait_for_flush(5)


def test_several_events_close_together_go_out_as_one_batch(sharing, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    for _ in range(5):
        usage.emit("app_opened")
    assert usage.wait_for_flush(5)
    assert [len(b) for b in transport.batches] == [5]


def test_nothing_is_scheduled_without_consent_or_without_a_transport(app_data, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    usage.emit("app_opened")                               # no consent: nothing queued, nothing scheduled
    assert usage.schedule_flush() is False
    UsageStore().set_consent("share")
    monkeypatch.setattr(usage, "get_transport", lambda: None)
    UsageStore().record("app_opened")
    assert usage.schedule_flush() is False
    assert transport.events == []


def test_a_failing_network_never_raises_and_the_events_stay_for_later(sharing, monkeypatch):
    def offline(batch):
        raise OSError("offline")

    monkeypatch.setattr(usage, "get_transport", lambda: offline)
    usage.emit("app_opened")
    assert usage.wait_for_flush(5)
    assert UsageStore().pending == 1


def test_the_flush_timer_is_a_daemon_so_it_never_keeps_a_process_alive(sharing, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    monkeypatch.setattr(usage, "FLUSH_DELAY", 30)
    usage.emit("app_opened")
    timer = usage._pending_timer
    assert timer is not None and timer.daemon is True
    usage.cancel_pending_flush()


# --- short-lived CLI runs: hand over to a detached process and exit at once -------------------------------------------
class FakePopen:
    calls: list[tuple] = []

    def __init__(self, *args, **kwargs):
        FakePopen.calls.append((args, kwargs))


@pytest.fixture
def popen(monkeypatch):
    FakePopen.calls = []
    monkeypatch.setattr(usage.subprocess, "Popen", FakePopen)
    return FakePopen


def test_a_cli_run_with_pending_events_starts_one_detached_flush_and_does_not_wait(sharing, popen, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    UsageStore().record("app_opened")
    assert usage.flush_detached() is True
    (args, kwargs), = popen.calls
    command = args[0]
    assert command[1:] == ["-m", "mdpipeline", "usage", "flush"]
    assert kwargs["start_new_session"] is True and kwargs["stdin"] is usage.subprocess.DEVNULL
    assert kwargs["stdout"] is usage.subprocess.DEVNULL and kwargs["stderr"] is usage.subprocess.DEVNULL


def test_it_is_throttled_so_a_busy_cron_does_not_start_a_process_per_run(sharing, popen, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    UsageStore().record("app_opened")
    assert usage.flush_detached() is True
    assert usage.flush_detached() is False
    assert len(popen.calls) == 1


def test_it_does_nothing_without_consent_events_or_a_transport(app_data, popen, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    assert usage.flush_detached() is False                                # no consent
    UsageStore().set_consent("share")
    assert usage.flush_detached() is False                                # nothing queued
    UsageStore().record("app_opened")
    monkeypatch.setattr(usage, "get_transport", lambda: None)
    assert usage.flush_detached() is False                                # no transport
    assert popen.calls == []


def test_a_failure_to_start_the_helper_never_raises(sharing, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    UsageStore().record("app_opened")

    def broken(*a, **k):
        raise OSError("no fork")

    monkeypatch.setattr(usage.subprocess, "Popen", broken)
    assert usage.flush_detached() is False


def test_the_usage_flush_command_sends_what_is_queued(sharing, monkeypatch, capsys):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().record("app_opened")
    assert main(["usage", "flush"]) == 0
    assert [e["event"] for e in transport.events] == ["app_opened"]
    assert UsageStore().pending == 0


def test_every_cli_command_ends_by_handing_pending_events_over(sharing, popen, monkeypatch, capsys):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    UsageStore().record("app_opened")
    assert main(["github-app", "show"]) == 0
    assert len(popen.calls) == 1


def test_the_flush_command_itself_and_the_mcp_server_do_not_start_another_helper(sharing, popen, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    UsageStore().record("app_opened")
    main(["usage", "flush"])
    assert popen.calls == []
    assert cli.starts_usage_helper("mcp") is False and cli.starts_usage_helper("ui") is False
    assert cli.starts_usage_helper("usage") is False and cli.starts_usage_helper("sync") is True
