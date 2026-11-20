"""MD-239: on Windows a read of usage.json while the app replaces it raises PermissionError for a moment. The store retries instead of seeing an empty file."""
from __future__ import annotations

from pathlib import Path

import pytest

from mdpipeline import fsretry
from mdpipeline.usage import UsageStore


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


def refuse_reads(monkeypatch, times: int = 1, alternate: bool = False) -> dict:
    """Make reading usage.json raise PermissionError (as Windows does mid-replace) the first ``times`` times, or every other read when ``alternate``."""
    real, calls = Path.read_text, {"n": 0}

    def read_text(self, *a, **k):
        if self.name == "usage.json":
            calls["n"] += 1
            if (calls["n"] % 2 == 1) if alternate else calls["n"] <= times:
                raise PermissionError(5, "Access is denied", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", read_text)
    return calls


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(fsretry, "retry_enabled", lambda: True)


def test_a_read_refused_for_a_moment_on_windows_is_retried_and_sees_the_stored_choice(windows, monkeypatch):
    UsageStore().set_consent("share")
    calls = refuse_reads(monkeypatch, times=2)
    assert UsageStore().consent == "share" and calls["n"] == 3


def test_a_read_refused_on_linux_is_not_retried_and_behaves_as_before(monkeypatch):
    UsageStore().set_consent("share")
    calls = refuse_reads(monkeypatch, times=1)
    assert UsageStore().consent != "share" and calls["n"] == 1


def test_a_refused_read_during_decide_cannot_count_the_first_answer_twice(windows, monkeypatch):
    store, transport = UsageStore(), Recorder()
    store.decide("share", transport)
    refuse_reads(monkeypatch, alternate=True)                  # every first read attempt lands in the middle of a replace
    store.decide("share", transport)
    assert [e["properties"]["outcome"] for e in transport.events if e["event"] == "consent_outcome"] == ["share"]


def test_a_refused_read_does_not_lose_the_first_answer(windows, monkeypatch):
    store = UsageStore()
    store.decide("decline")
    refuse_reads(monkeypatch, times=1)
    store.decide("share")                                      # a later answer: reads must still see the stored store, not an empty one
    assert UsageStore().counter_eligible is True
