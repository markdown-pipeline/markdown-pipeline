"""MD-165: scheduled sync comes back after a reboot on Linux with no desktop login (server, SSH box, WSL 2), with no user step.

AC1 systemd user service; AC2 the one linger command is explained, never run; AC3 self-healing start; AC4 one process;
AC5 off/uninstall remove everything; AC6 no change elsewhere; AC7 background_started {how}."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from mdpipeline import autostart, background, cli, scheduler, usage
from mdpipeline.schedulestore import SchedulerStore
from mdpipeline.usage import UsageStore


class FakeSystemd:
    """Stands in for `systemctl --user` / `loginctl`: records the calls, answers like a box where systemd works."""

    def __init__(self, available=True, linger="no"):
        self.available, self.linger, self.calls = available, linger, []

    def __call__(self, *args):
        self.calls.append(args)
        if not self.available:
            return None
        out = f"Linger={self.linger}\n" if args[0] == "loginctl" else ""
        return SimpleNamespace(returncode=0, stdout=out)


@pytest.fixture
def systemd(app_data, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    fake = FakeSystemd()
    monkeypatch.setattr(autostart, "_run", fake)
    return fake


def unit(tmp_path):
    return tmp_path / ".config/systemd/user/mdpipeline-background.service"


# --- AC1: the user service ---------------------------------------------------------------------------------------------
@pytest.mark.windows_unsupported("systemd and linger exist only on Linux; Windows has no such text (see test_windows_background)")
def test_the_service_unit_runs_the_background_command_from_the_user_manager_and_restarts_on_failure(systemd, tmp_path):
    assert autostart.enable_user_service(home=tmp_path, python="/usr/bin/python3") is True
    text = unit(tmp_path).read_text()
    assert "ExecStart=/usr/bin/python3 -m mdpipeline background --started-by user_service" in text
    assert "WantedBy=default.target" in text and "Restart=on-failure" in text


@pytest.mark.windows_unsupported("systemd and linger exist only on Linux; Windows has no such text (see test_windows_background)")
def test_enabling_the_service_reloads_systemd_and_enables_it(systemd, tmp_path):
    autostart.enable_user_service(home=tmp_path, python="/p")
    assert ("systemctl", "--user", "daemon-reload") in systemd.calls
    assert ("systemctl", "--user", "enable", "mdpipeline-background.service") in systemd.calls


def test_without_systemd_nothing_is_written_and_it_says_so(app_data, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(available=False))
    assert autostart.enable_user_service(home=tmp_path, python="/p") is False
    assert not unit(tmp_path).exists()


def test_a_non_linux_platform_never_touches_systemd(systemd, tmp_path):
    assert autostart.enable_user_service(home=tmp_path, python="/p", platform="darwin") is False
    assert systemd.calls == []


def test_turning_background_sync_on_enables_the_login_item_and_the_user_service(app_data, monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "enable", lambda **kw: calls.append("login_item"))
    monkeypatch.setattr(autostart, "enable_user_service", lambda **kw: calls.append("user_service") or True)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: calls.append("start") or True)
    assert scheduler.ensure_background()
    assert calls == ["login_item", "user_service", "start"]


# --- AC5: off and uninstall remove everything --------------------------------------------------------------------------
@pytest.mark.windows_unsupported("systemd and linger exist only on Linux; Windows has no such text (see test_windows_background)")
def test_disabling_the_service_removes_the_unit_and_disables_it(systemd, tmp_path):
    autostart.enable_user_service(home=tmp_path, python="/p")
    assert autostart.disable_user_service(home=tmp_path) is True
    assert not unit(tmp_path).exists()
    assert ("systemctl", "--user", "disable", "mdpipeline-background.service") in systemd.calls
    assert systemd.calls[-1] == ("systemctl", "--user", "daemon-reload")


def test_disabling_with_no_service_is_a_quiet_no_op(systemd, tmp_path):
    assert autostart.disable_user_service(home=tmp_path) is False
    assert systemd.calls == []


def test_schedule_background_off_removes_the_login_item_and_the_service(app_data, monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "disable", lambda **kw: calls.append("login_item") or True)
    monkeypatch.setattr(autostart, "disable_user_service", lambda **kw: calls.append("user_service") or True)
    scheduler.turn_off_background()
    assert calls == ["login_item", "user_service"]


@pytest.mark.windows_unsupported("systemd and linger exist only on Linux; Windows has no such text (see test_windows_background)")
def test_uninstall_leaves_nothing_enabled_on_a_real_service_file(systemd, app_data, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    autostart.enable()
    autostart.enable_user_service()
    assert unit(tmp_path).exists() and autostart.is_enabled()
    assert cli.main(["uninstall"]) == 0
    assert not unit(tmp_path).exists() and not autostart.is_enabled()


# --- AC2: the one command, explained, never run ------------------------------------------------------------------------
@pytest.mark.windows_unsupported("systemd and linger exist only on Linux; Windows has no such text (see test_windows_background)")
def test_the_linger_hint_shows_on_a_systemd_box_where_linger_is_off(systemd):
    assert autostart.linger_hint() is True


def test_no_linger_hint_when_linger_is_on(app_data, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="yes"))
    assert autostart.linger_hint() is False


def test_no_linger_hint_without_systemd(app_data, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(available=False))
    assert autostart.linger_hint() is False


def test_the_app_never_runs_enable_linger_itself(systemd, tmp_path):
    autostart.enable_user_service(home=tmp_path, python="/p")
    autostart.linger_hint()
    assert not any("enable-linger" in " ".join(c) for c in systemd.calls)
    assert not any(c[0] == "sudo" for c in systemd.calls)


@pytest.mark.windows_unsupported("systemd and linger exist only on Linux; Windows has no such text (see test_windows_background)")
def test_the_background_view_carries_the_hint_only_when_background_is_on(systemd, app_data):
    from mdpipeline.uiapi import background_view

    assert background_view()["linger_hint"] is True
    scheduler.set_background(False)
    assert background_view()["linger_hint"] is False


# --- AC3: self-healing fallback ----------------------------------------------------------------------------------------
@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "start_now", lambda python=None, how="app_start": calls.append(how) or True)
    return calls


def turn_on():
    with SchedulerStore().update() as data:
        data["background"].update({"enabled": True, "announced": True})


def test_background_on_but_not_running_is_started_by_the_next_start_of_the_app(app_data, started):
    turn_on()
    assert background.ensure_running() is True and started == ["app_start"]


def test_nothing_starts_when_it_is_already_running(app_data, started, monkeypatch):
    turn_on()
    monkeypatch.setattr(background, "is_running", lambda: True)
    assert background.ensure_running() is False and started == []


def test_nothing_starts_after_the_opt_out(app_data, started):
    turn_on()
    scheduler.set_background(False)
    assert background.ensure_running() is False and started == []


def test_nothing_starts_before_the_first_sync_turned_it_on(app_data, started):
    assert background.ensure_running() is False and started == []


@pytest.mark.parametrize("command", ["status", "ui", "start", "sync", "schedule", "mcp", "vault"])
def test_any_mdpipeline_command_starts_it_when_it_is_on_but_not_running(app_data, monkeypatch, command):
    calls = []
    monkeypatch.setattr(background, "ensure_running", lambda **kw: calls.append(kw))
    cli.self_heal_background(command)
    assert calls == [{}]


@pytest.mark.parametrize("command", ["background", "uninstall"])
def test_the_background_and_uninstall_commands_never_start_it(app_data, monkeypatch, command):
    calls = []
    monkeypatch.setattr(background, "ensure_running", lambda **kw: calls.append(kw))
    cli.self_heal_background(command)
    assert calls == []


def test_a_failing_self_heal_never_breaks_the_command(app_data, monkeypatch):
    def boom(**kw):
        raise OSError("no")

    monkeypatch.setattr(background, "ensure_running", boom)
    cli.self_heal_background("status")


def test_start_now_tells_the_process_how_it_was_started(monkeypatch):
    seen = []
    monkeypatch.delenv("MDPIPELINE_NO_BACKGROUND_START", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.append(cmd))
    assert autostart.start_now("/p", how="app_start") is True
    assert seen == [["/p", "-m", "mdpipeline", "background", "--started-by", "app_start"]]


# --- AC4: one process --------------------------------------------------------------------------------------------------
def test_a_second_background_process_exits_at_once_and_emits_nothing(app_data, monkeypatch):
    UsageStore().set_consent("share")
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    inner = []

    def try_second(_):
        inner.append(background.run(max_ticks=1, sleep=lambda s: None, interval=1, how="app_start"))

    background.run(max_ticks=2, sleep=try_second, interval=1, how="user_service")
    assert inner == [False]
    assert [own(e) for e in queued("background_started")] == [{"how": "user_service"}]


# --- AC6: desktop Linux and macOS are unchanged ------------------------------------------------------------------------
def test_the_desktop_entry_and_the_launch_agent_still_run_the_background_command(tmp_path):
    entry = autostart.enable(home=tmp_path, platform="linux", python="/p").read_text()
    assert "Exec=/p -m mdpipeline background --started-by login_item" in entry
    import plistlib

    plist = plistlib.loads(autostart.enable(home=tmp_path, platform="darwin", python="/p").read_bytes())
    assert plist["ProgramArguments"][:4] == ["/p", "-m", "mdpipeline", "background"] and plist["RunAtLoad"] is True


# --- AC7: the usage event ----------------------------------------------------------------------------------------------
def queued(*names):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def own(e):
    """The event's own properties; MD-168's desktop_session / linger_enabled have their own tests (test_md168_measurement.py)."""
    return {k: v for k, v in e["properties"].items()
            if k not in ("app_version", "deployment", "session_id", "is_internal", "desktop_session", "linger_enabled")}


@pytest.mark.parametrize("how", ["login_item", "user_service", "app_start", "manual"])
def test_the_process_reports_how_it_was_started(app_data, monkeypatch, how):
    UsageStore().set_consent("share")
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    background.run(max_ticks=1, sleep=lambda s: None, how=how)
    assert [own(e) for e in queued("background_started")] == [{"how": how}]


def test_the_command_passes_started_by_through(app_data, monkeypatch):
    seen = []
    monkeypatch.setattr(background, "run", lambda **kw: seen.append(kw) or True)
    assert cli.main(["background", "--started-by", "user_service"]) == 0
    assert cli.main(["background"]) == 0
    assert seen == [{"how": "user_service"}, {"how": "manual"}]


def test_an_unknown_value_is_refused_so_the_event_stays_in_its_enum(app_data, capsys):
    with pytest.raises(SystemExit):
        cli.main(["background", "--started-by", "somewhere"])


def test_the_event_is_in_the_usage_doc():
    from pathlib import Path

    doc = (Path(__file__).parent.parent / "docs" / "usage-events.md").read_text()
    assert "background_started" in doc and "user_service" in doc


# --- AC2/AC3 copy (sent to product for marketing review before release) -----------------------------------------------
def test_settings_explains_the_one_linger_command_and_the_self_restart():
    from pathlib import Path

    js = (Path(__file__).parent.parent / "src/mdpipeline/ui_static/app.js").read_text()
    assert "loginctl enable-linger $USER" in autostart.LINGER_LINE and "bg.linger_hint" in js and "bg.linger_text" in js
    assert "Opening the app or running any mdpipeline command starts it again" in js
    assert "It starts at your next login" not in js


def test_the_readme_explains_the_linger_command():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "reference.md").read_text()
    assert "loginctl enable-linger $USER" in text and "systemd user service" in text
