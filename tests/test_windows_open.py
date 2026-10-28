"""MD-182 AC1: "Open" (a file, or the folder a pipeline writes to) uses the Windows default app on Windows.

Runs on every OS with a pretend Windows: `os.startfile` exists only on Windows, so the tests give it a fake.
"""
import os
import subprocess
import sys

import pytest

from mdpipeline import uiapi


@pytest.fixture
def fake_windows(monkeypatch):
    started, popens = [], []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", started.append, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popens.append(a))
    return started, popens


def test_on_windows_open_uses_the_default_app_for_a_file_and_runs_no_command(fake_windows):
    started, popens = fake_windows
    uiapi.os_open("C:\\Users\\me\\brain\\raw\\p\\a note.md")
    assert started == ["C:\\Users\\me\\brain\\raw\\p\\a note.md"] and popens == []


def test_on_windows_open_uses_the_default_app_for_a_folder(fake_windows):
    started, _ = fake_windows
    uiapi.os_open("C:\\Users\\me\\brain\\raw")
    assert started == ["C:\\Users\\me\\brain\\raw"]


def test_on_windows_a_failed_open_raises_oserror_so_the_existing_error_text_is_shown(monkeypatch):
    def refuse(path):
        raise FileNotFoundError(2, "No application is associated with the specified file", path)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    with pytest.raises(OSError):
        uiapi.os_open("C:\\x.md")


def test_open_file_reports_a_windows_failure_as_ok_false_with_the_message(monkeypatch, brain, make_source, add_pipeline):
    from mdpipeline.config import Pipeline
    from mdpipeline.engine import sync_pipeline

    add_pipeline(Pipeline.new("p", [str(make_source("docs", {"a.md": "A"}))]))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    rel = next(p for p in (brain.root / "raw").rglob("*.md")).relative_to(brain.root).as_posix()

    def refuse(path):
        raise OSError("no default app")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    res = uiapi.open_file(brain, rel)
    assert res["ok"] is False and "no default app" in res["error"]


def test_off_windows_the_commands_are_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(sys, "platform", "linux")
    uiapi.os_open("/tmp/a.md")
    monkeypatch.setattr(sys, "platform", "darwin")
    uiapi.os_open("/tmp/a.md")
    assert calls == [["xdg-open", "/tmp/a.md"], ["open", "/tmp/a.md"]]
