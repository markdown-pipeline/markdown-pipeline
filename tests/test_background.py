"""MD-121 AC2: a background process that keeps ticking when the UI is closed; only one at a time; never dies of one bad tick."""
import time

import pytest

from mdpipeline import background, scheduler


def test_it_ticks_until_told_to_stop(app_data, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: calls.append(now) or [])
    sleeps = []
    background.run(max_ticks=3, sleep=sleeps.append, interval=60)
    assert len(calls) == 3 and sleeps == [60, 60]


def test_a_tick_that_raises_does_not_stop_the_process(app_data, monkeypatch):
    seen = []

    def tick_all(now=None):
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(scheduler, "tick_all", tick_all)
    background.run(max_ticks=3, sleep=lambda s: None, interval=1)
    assert len(seen) == 3


def test_a_second_process_does_not_start_while_the_first_runs(app_data, monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    inner = []

    def sleep_and_try_second(_):
        inner.append(background.run(max_ticks=1, sleep=lambda s: None, interval=1))

    assert background.run(max_ticks=2, sleep=sleep_and_try_second, interval=1) is True
    assert inner == [False]


def test_is_running_reflects_the_lock(app_data, monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    states = []
    background.run(max_ticks=2, sleep=lambda s: states.append(background.is_running()), interval=1)
    assert states == [True] and background.is_running() is False


def test_the_lock_is_released_when_the_process_ends(app_data, monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    assert background.run(max_ticks=1, sleep=lambda s: None, interval=1) is True
    assert background.run(max_ticks=1, sleep=lambda s: None, interval=1) is True
