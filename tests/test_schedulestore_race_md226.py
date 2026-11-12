"""MD-226: saving the schedule never shows a raw ``PermissionError [WinError 5]`` when something is reading ``scheduler.json`` at the same moment.

Mechanism: ``os.replace`` is retried a short, bounded time on ``PermissionError`` (what Windows raises while the file is open elsewhere); a read that
meets the same refusal retries too, so it never answers with the defaults by mistake. If the save still fails the old file is kept and the error is a
plain sentence with a next step. The Windows refusal is simulated, so Linux CI proves the logic; the stress test runs for real on both platforms."""
from __future__ import annotations

import json
import os
import threading

import pytest

from mdpipeline import schedulestore
from mdpipeline.schedulestore import SchedulerStore, ScheduleSaveError


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(tmp_path))
    s = SchedulerStore()
    with s.update() as data:
        data["runtime"]["n"] = 1
    return s


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(schedulestore.fsretry.time, "sleep", lambda s: None)       # no real waiting in the simulated cases
    monkeypatch.setattr(schedulestore.fsretry, "retry_enabled", lambda: True)      # MD-229: the refusal is simulated as Windows


def _refuse(times: int, real=os.replace):
    calls = {"n": 0}

    def replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= times:
            raise PermissionError(5, "Access is denied", str(dst))
        return real(src, dst)
    return replace, calls


def test_a_refused_replace_is_retried_and_the_change_is_saved(store, no_wait, monkeypatch):
    replace, calls = _refuse(3)
    monkeypatch.setattr(os, "replace", replace)
    with store.update() as data:
        data["runtime"]["n"] = 2
    assert calls["n"] == 4 and store.read()["runtime"]["n"] == 2


def test_a_replace_that_keeps_being_refused_gives_a_plain_message_and_keeps_the_old_schedule(store, no_wait, monkeypatch):
    monkeypatch.setattr(os, "replace", _refuse(10**6)[0])
    with pytest.raises(ScheduleSaveError) as err:
        with store.update() as data:
            data["runtime"]["n"] = 2
    text = str(err.value)
    assert "WinError" not in text and "PermissionError" not in text and "Access is denied" not in text and "try again" in text.lower()
    monkeypatch.undo()
    assert store.read()["runtime"]["n"] == 1                                       # the previous schedule is still valid
    assert not list(store._path.parent.glob("*.tmp"))                              # no stray temp file


def test_the_retry_is_bounded_to_about_one_second(store, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(schedulestore.fsretry.time, "sleep", slept.append)
    monkeypatch.setattr(schedulestore.fsretry, "retry_enabled", lambda: True)      # MD-229: simulated Windows
    monkeypatch.setattr(os, "replace", _refuse(10**6)[0])
    with pytest.raises(ScheduleSaveError):
        with store.update():
            pass
    assert 0.5 <= sum(slept) <= 1.5


def test_a_read_that_is_refused_for_a_moment_retries_instead_of_answering_with_the_defaults(store, no_wait, monkeypatch):
    real, calls = type(store._path).read_text, {"n": 0}

    def read_text(self, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(self, *a, **k)
    monkeypatch.setattr(type(store._path), "read_text", read_text)
    assert store.read()["runtime"]["n"] == 1


def test_a_missing_or_broken_file_still_reads_as_the_defaults(store):
    store._path.write_text("{not json", encoding="utf-8")
    assert store.read()["runtime"] == {}
    store._path.unlink()
    assert store.read()["brains"] == []


def test_many_reads_during_many_updates_never_fail_and_never_see_a_partial_file(store):
    errors: list[BaseException] = []
    stop = threading.Event()
    seen: list[int] = []

    def reader():
        try:
            while not stop.is_set():
                n = SchedulerStore().read()["runtime"]["n"]            # KeyError if a read ever answered with the defaults
                seen.append(n)
        except BaseException as e:                                     # noqa: BLE001 - the test reports whatever happened
            errors.append(e)

    def writer(k: int):
        try:
            for _ in range(40):
                with SchedulerStore().update() as data:
                    data["runtime"]["n"] = data["runtime"]["n"] + 1
        except BaseException as e:                                     # noqa: BLE001
            errors.append(e)

    readers = [threading.Thread(target=reader) for _ in range(6)]
    writers = [threading.Thread(target=writer, args=(k,)) for k in range(3)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()
    assert errors == [] and seen and store.read()["runtime"]["n"] == 1 + 3 * 40
    assert json.loads(store._path.read_text(encoding="utf-8"))["runtime"]["n"] == 121


def test_the_ui_shows_a_plain_message_not_a_python_error_when_the_schedule_cannot_be_saved(brain, monkeypatch):
    from uihelper import running_ui
    monkeypatch.setattr(schedulestore.fsretry.time, "sleep", lambda s: None)
    monkeypatch.setattr(schedulestore.fsretry, "retry_enabled", lambda: True)      # MD-229: the refusal is simulated as Windows
    with running_ui(brain) as ui:
        monkeypatch.setattr(os, "replace", _refuse(10**6)[0])
        status, body = ui.post("/api/background", {"enabled": False})
        monkeypatch.undo()
    assert status == 409
    assert "WinError" not in body["error"] and "PermissionError" not in body["error"] and "ScheduleSaveError" not in body["error"]
    assert "try again" in body["error"].lower()
