"""MD-171: a login item written before `--started-by` existed makes the background process report `how=manual`. When the app starts after a
version change, or the background process starts, an EXISTING login item whose content differs from what is written today is rewritten.
Never created when missing, never touched when background sync is off or the item is already current."""
import os
import plistlib

import pytest

from mdpipeline import autostart, background, scheduler, usage
from mdpipeline.usage import UsageStore

PLATFORMS = ["linux", "darwin", "win32"]
PY = "/opt/mdpipeline/bin/python"


def old_content(kind: str) -> bytes:
    """The pre-v0.1.8 login item: the same command, without `--started-by login_item`."""
    if kind == "linux":
        return b"[Desktop Entry]\nType=Application\nName=Markdown Pipeline (background sync)\nExec=" + PY.encode() + b" -m mdpipeline background\nTerminal=false\n"
    if kind == "darwin":
        return plistlib.dumps({"Label": autostart.LABEL, "ProgramArguments": [PY, "-m", "mdpipeline", "background"], "RunAtLoad": True, "KeepAlive": True})
    return f'@echo off\r\nstart "" /B "{PY}" -m mdpipeline background\r\n'.encode()


@pytest.fixture(autouse=True)
def platform_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def put(tmp_path, platform, content):
    path = autostart.item_path(home=tmp_path, platform=platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (1_000_000, 1_000_000))
    return path


# --- AC1/AC3/AC4: the rewrite itself, on every platform -----------------------------------------------------------------------------------
@pytest.mark.parametrize("platform", PLATFORMS)
def test_an_old_login_item_is_rewritten_to_the_current_content(tmp_path, platform):
    path = put(tmp_path, platform, old_content(platform))
    assert autostart.refresh_if_outdated(home=tmp_path, platform=platform, python=PY) is True
    assert path.read_bytes() == autostart._content(platform, PY)
    assert b"login_item" in path.read_bytes()


@pytest.mark.parametrize("platform", PLATFORMS)
def test_an_up_to_date_login_item_is_not_touched(tmp_path, platform):
    path = put(tmp_path, platform, autostart._content(platform, PY))
    assert autostart.refresh_if_outdated(home=tmp_path, platform=platform, python=PY) is False
    assert path.stat().st_mtime_ns == 1_000_000 * 10**9          # not even rewritten with the same bytes


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_missing_login_item_stays_missing(tmp_path, platform):
    assert autostart.refresh_if_outdated(home=tmp_path, platform=platform, python=PY) is False
    assert not autostart.item_path(home=tmp_path, platform=platform).exists()


def test_an_unsupported_platform_changes_nothing_and_does_not_raise(tmp_path):
    assert autostart.refresh_if_outdated(home=tmp_path, platform="plan9", python=PY) is False


# --- AC2: background sync off -----------------------------------------------------------------------------------------------------------
def current_platform():
    return autostart._kind(__import__("sys").platform)


def put_old_here(tmp_path):
    """An old login item in the sandboxed HOME of this test (the real platform, since the callers use sys.platform)."""
    kind = current_platform()
    path = autostart.item_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(old_content(kind).replace(PY.encode(), __import__("sys").executable.encode()))
    return path


def test_scheduler_refresh_rewrites_an_old_item_when_background_sync_is_on(tmp_path):
    path = put_old_here(tmp_path)
    assert scheduler.background_enabled() is True
    assert scheduler.refresh_login_item() is True
    assert path.read_bytes() == autostart._content(current_platform(), __import__("sys").executable)


def test_scheduler_refresh_changes_nothing_when_background_sync_is_off(tmp_path):
    path = put_old_here(tmp_path)
    before = path.read_bytes()
    scheduler.set_background(False)
    assert scheduler.refresh_login_item() is False
    assert path.read_bytes() == before


def test_scheduler_refresh_creates_nothing_when_there_is_no_item(tmp_path):
    assert scheduler.refresh_login_item() is False
    assert not autostart.item_path().exists()


# --- AC1: the two moments -------------------------------------------------------------------------------------------------------------
def test_starting_the_background_process_refreshes_an_old_login_item(tmp_path, monkeypatch):
    path = put_old_here(tmp_path)
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    assert background.run(max_ticks=1, sleep=lambda s: None, interval=1, how="manual") is True
    assert path.read_bytes() == autostart._content(current_platform(), __import__("sys").executable)


def test_starting_the_background_process_with_sync_off_leaves_the_login_item_alone(tmp_path, monkeypatch):
    path = put_old_here(tmp_path)
    before = path.read_bytes()
    scheduler.set_background(False)
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    background.run(max_ticks=1, sleep=lambda s: None, interval=1, how="manual")
    assert path.read_bytes() == before


def test_opening_the_app_after_an_update_refreshes_an_old_login_item_even_without_usage_consent(tmp_path):
    from mdpipeline import usage_hooks

    path = put_old_here(tmp_path)
    UsageStore().swap_version("0.1.0")                 # the previous run was an older version
    usage_hooks.app_opened()          # no consent was ever given in this sandbox: the refresh does not depend on it
    assert path.read_bytes() == autostart._content(current_platform(), __import__("sys").executable)


def test_opening_the_app_after_an_update_with_sync_off_leaves_the_login_item_alone(tmp_path):
    from mdpipeline import usage_hooks

    path = put_old_here(tmp_path)
    before = path.read_bytes()
    scheduler.set_background(False)
    UsageStore().swap_version("0.1.0")
    usage_hooks.app_opened()
    assert path.read_bytes() == before


def test_opening_the_app_after_an_update_creates_no_login_item_when_there_is_none(tmp_path):
    from mdpipeline import usage_hooks

    UsageStore().swap_version("0.1.0")
    usage_hooks.app_opened()
    assert not autostart.item_path().exists()
