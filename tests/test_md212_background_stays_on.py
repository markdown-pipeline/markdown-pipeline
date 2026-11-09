"""MD-212 (AC1, AC2, AC9): background sync stays on after a reboot. A missing login item is re-created while background is on (never while it is off),
and each self-heal is counted (background_self_healed: what = login_item | process, via = cli | app | mcp) with no paths or names."""
import pytest

from mdpipeline import autostart, background, scheduler
from mdpipeline.schedulestore import SchedulerStore
from mdpipeline.usage import UsageStore

from test_background_headless import own, queued

PLATFORMS = ["linux", "darwin", "win32"]
PY = "/opt/mdpipeline/bin/python"


@pytest.fixture(autouse=True)
def platform_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def background_on(announced: bool = True, enabled: bool = True) -> None:
    with SchedulerStore().update() as data:
        data["background"]["enabled"] = enabled
        data["background"]["announced"] = announced


# --- AC1: autostart.ensure_item ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("platform", PLATFORMS)
def test_ensure_item_creates_a_missing_login_item_with_the_same_content_as_enable(tmp_path, platform):
    assert autostart.ensure_item(home=tmp_path, platform=platform, python=PY) is True
    assert autostart.item_path(home=tmp_path, platform=platform).read_bytes() == autostart._content(platform, PY)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ensure_item_leaves_an_existing_item_alone(tmp_path, platform):
    autostart.enable(home=tmp_path, platform=platform, python=PY)
    assert autostart.ensure_item(home=tmp_path, platform=platform, python=PY) is False


def test_ensure_item_on_an_unsupported_platform_changes_nothing(tmp_path):
    assert autostart.ensure_item(home=tmp_path, platform="plan9", python=PY) is False


# --- AC1/AC2: scheduler.ensure_login_item -------------------------------------------------------------------------------------------
def test_a_missing_login_item_is_re_created_while_background_is_on(app_data):
    background_on()
    assert scheduler.ensure_login_item() is True
    assert autostart.item_path().is_file()


def test_nothing_is_re_created_while_background_is_off(app_data):
    background_on(enabled=False)
    assert scheduler.ensure_login_item() is False
    assert not autostart.item_path().exists()


def test_nothing_is_created_before_background_was_announced(app_data):
    background_on(announced=False)
    assert scheduler.ensure_login_item() is False
    assert not autostart.item_path().exists()


# --- AC9: background_self_healed ------------------------------------------------------------------------------------------------------
def test_re_creating_the_login_item_emits_one_event_with_no_paths(app_data):
    UsageStore().set_consent("share")
    background_on()
    scheduler.ensure_login_item(via="cli")
    scheduler.ensure_login_item(via="cli")                 # now it exists: nothing more to heal
    assert [own(e) for e in queued("background_self_healed")] == [{"what": "login_item", "via": "cli"}]


@pytest.mark.parametrize("via", ["cli", "app", "mcp"])
def test_starting_a_dead_background_process_emits_what_process_and_how(app_data, monkeypatch, via):
    UsageStore().set_consent("share")
    background_on()
    monkeypatch.setattr(background, "is_running", lambda: False)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: True)
    assert background.ensure_running(via=via) is True
    assert [own(e) for e in queued("background_self_healed") if own(e)["what"] == "process"] == [{"what": "process", "via": via}]


def test_a_running_process_is_not_a_self_heal(app_data, monkeypatch):
    UsageStore().set_consent("share")
    background_on()
    autostart.enable()                                      # the login item is there: only the process could need healing
    monkeypatch.setattr(background, "is_running", lambda: True)
    background.ensure_running(restart_outdated=False)
    assert queued("background_self_healed") == []


def test_background_off_is_not_a_self_heal(app_data, monkeypatch):
    UsageStore().set_consent("share")
    background_on(enabled=False)
    monkeypatch.setattr(background, "is_running", lambda: False)
    assert background.ensure_running() is False
    assert queued("background_self_healed") == []
