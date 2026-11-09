"""MD-208 / MD-219 / MD-221: status never contradicts itself while background sync restarts or is down (one start action, never "mdpipeline background"),
`mdpipeline update` run outside a brain finishes without an "Error:" line, prints in the right order, and `mdpipeline --version` works from anywhere."""
import subprocess
import sys
import time

import pytest

import mdpipeline
from mdpipeline import autostart, background, cli, lifecycle
from mdpipeline.cli import main

from test_md212_not_running import RESTART, set_state

NOW = 1_800_000_000.0


@pytest.fixture
def gap(monkeypatch, app_data):
    """Background is on and announced; the old process has just ended and the new one is waiting for the lock: nothing holds it for a moment."""
    set_state(last_tick_at=time.time() - 5)
    monkeypatch.setattr(background, "is_running", lambda: False)
    monkeypatch.setattr(autostart, "start_now", lambda **kw: False)
    monkeypatch.setattr(autostart, "ensure_item", lambda **kw: False)


def schedule(brain, capsys):
    main(["--brain", str(brain.root), "schedule"])
    return capsys.readouterr().out


# --- MD-208: the restart gap -----------------------------------------------------------------------------------------------------------------
def test_while_the_replacement_starts_schedule_says_restarting_not_not_running(gap, brain, capsys):
    background.mark_restart_requested()
    out = schedule(brain, capsys)
    assert f"Background sync: on, restarting on version {mdpipeline.__version__}" in out
    assert "not running" not in out and "mdpipeline background" not in out.replace(RESTART, "")


def test_the_app_state_is_not_not_running_while_the_replacement_starts(gap, app_data):
    background.mark_restart_requested()
    assert background.restart_in_progress() is True


def test_when_the_replacement_never_comes_the_not_running_text_shows_after_the_window(gap, brain, capsys):
    background.mark_restart_requested(time.time() - background.RESTART_PENDING_SECONDS - 5)
    out = schedule(brain, capsys)
    assert "restarting" not in out and "Background sync: on but not running" in out


def test_without_any_restart_request_a_gap_is_not_a_restart(gap, app_data):
    assert background.restart_in_progress() is False


def test_a_running_process_is_not_a_restart_in_progress(gap, monkeypatch):
    background.mark_restart_requested()
    monkeypatch.setattr(background, "is_running", lambda: True)
    assert background.restart_in_progress() is False


# --- MD-219: one way to start it again -------------------------------------------------------------------------------------------------------
def test_not_running_status_offers_only_the_restart_command(gap, brain, capsys):
    set_state(last_tick_at=time.time() - 5)
    out = schedule(brain, capsys)
    assert "Background sync: on but not running" in out
    assert RESTART in out
    assert "mdpipeline background" not in out.replace(RESTART, "")


def test_not_running_with_the_notice_up_names_one_action_only(gap, brain, capsys):
    set_state(last_tick_at=None)               # long silence: the MD-212 notice shows too
    out = schedule(brain, capsys)
    assert "isn't running" in out
    assert "mdpipeline background" not in out.replace(RESTART, "")


def test_the_app_does_not_send_people_to_the_foreground_command():
    js = (cli.Path(cli.__file__).parent / "ui_static" / "app.js").read_text(encoding="utf-8")
    assert "${CLI} background`" not in js and "${CLI} background<" not in js


# --- MD-221: update outside a brain ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def nowhere(tmp_path, monkeypatch):
    """A folder with no brain at or above it."""
    d = tmp_path / "nowhere"
    d.mkdir()
    monkeypatch.chdir(d)
    from mdpipeline import install_info
    monkeypatch.setattr(install_info, "install_method", lambda: "uv")           # not a source checkout: `update` has a command to run
    return d


def test_background_restart_needs_no_brain(nowhere, app_data, monkeypatch, capsys):
    monkeypatch.setattr(background, "restart", lambda: "off")
    assert main(["schedule", "background", "restart"]) == 0
    assert "Error" not in capsys.readouterr().err


def test_update_outside_a_brain_prints_no_error_and_exits_zero(nowhere, app_data, monkeypatch, capsys):
    monkeypatch.setattr(lifecycle, "run_command", lambda command: 0)
    monkeypatch.setattr(cli, "_updated_line", lambda: "Updated to version 9.9.9.")
    monkeypatch.setattr(background, "restart", lambda: "off")
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: real_run(cmd, **kw) if cmd[:3] == [sys.executable, "-m", "mdpipeline"] else subprocess.CompletedProcess(cmd, 0))
    code = main(["update"])
    out = capsys.readouterr()
    assert code == 0 and "Error" not in out.out + out.err and "mdpipeline init" not in out.out + out.err


def test_update_prints_running_first_then_the_tools_output_then_the_final_line(nowhere, app_data, monkeypatch, capfd):
    def run(command):
        print("tool output", flush=False)
        return 0
    monkeypatch.setattr(lifecycle, "run_command", run)
    monkeypatch.setattr(cli, "_updated_line", lambda: "Updated to version 9.9.9.")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    main(["update"])
    out = capfd.readouterr().out
    assert out.index("Running:") < out.index("tool output") < out.index("Updated to version 9.9.9.")


def test_update_flushes_before_the_command_runs(nowhere, app_data, monkeypatch):
    seen = []
    monkeypatch.setattr(sys.stdout, "flush", lambda: seen.append("flush"))
    monkeypatch.setattr(lifecycle, "run_command", lambda command: seen.append("run") or 0)
    monkeypatch.setattr(cli, "_updated_line", lambda: "x")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    main(["update"])
    assert seen.index("flush") < seen.index("run")


# --- MD-221: --version -----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_prints_the_version_and_exits_zero_from_anywhere(flag, nowhere, app_data, capsys):
    with pytest.raises(SystemExit) as stop:
        main([flag])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"mdpipeline {mdpipeline.__version__}"
