"""MD-204: after `mdpipeline update` the background process runs the new version (without a login), `off` really stops it, and status says which
version runs. Real processes where a signal or a lock is the point; fakes only for the "start a new process" step."""
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import mdpipeline
from mdpipeline import autostart, background, scheduler
from mdpipeline.cli import main
from bg_helper import FakeBackgroundProcess

HERE = Path(__file__).parent


_BRAIN: list[str] = []


@pytest.fixture(autouse=True)
def _brain_for_cli(brain):
    _BRAIN[:] = ["--brain", str(brain.root)]


def run_cli(capsys, *argv):
    code = main([*_BRAIN, *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def started(monkeypatch):
    """Replace "start a new background process" with a recorder; returns the list of (how, wait_for_lock) it was asked for."""
    calls = []
    monkeypatch.setattr(autostart, "start_now", lambda python=None, how="app_start", wait_for_lock=0: calls.append((how, wait_for_lock)) or True)
    return calls


def announce():
    """Background sync is on for this person (the first sync turned it on)."""
    from mdpipeline.schedulestore import SchedulerStore

    with SchedulerStore().update() as data:
        data["background"]["announced"] = True


@pytest.fixture
def stops(monkeypatch):
    calls = []
    monkeypatch.setattr(background, "stop", lambda: calls.append("stop") or True)
    return calls


# --- the process says which version it runs -----------------------------------------------------------------------------------
def test_a_running_process_records_the_version_it_runs(monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    seen = []
    background.run(max_ticks=2, sleep=lambda s: seen.append(background.running_version()), interval=1)
    assert seen == [mdpipeline.__version__]


def test_nothing_running_means_no_version():
    assert background.running_version() is None and background.is_outdated() is False


def test_a_process_that_holds_the_lock_but_left_no_record_is_an_unknown_older_version():
    fake = FakeBackgroundProcess()
    fake.start()
    time.sleep(0.2)
    try:
        assert background.running_version() == background.UNKNOWN_VERSION and background.is_outdated() is True
    finally:
        fake.stop()


def test_a_record_left_by_a_dead_process_is_not_believed(monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    background.run(max_ticks=1, sleep=lambda s: None, interval=1)         # leaves its record behind, then ends
    fake = FakeBackgroundProcess()
    fake.start()
    time.sleep(0.2)
    try:
        assert background.running_version() == background.UNKNOWN_VERSION  # another process holds the lock now; the old record is not its own
    finally:
        fake.stop()


@pytest.mark.parametrize("running, installed, outdated", [("0.1.14", "0.1.15", True), ("0.1.15", "0.1.15", False), ("0.1.16", "0.1.15", False),
                                                          ("0.1.9", "0.1.10", True), (background.UNKNOWN_VERSION, "0.0.1", True)])
def test_only_an_older_running_version_counts_as_outdated(monkeypatch, running, installed, outdated):
    monkeypatch.setattr(mdpipeline, "__version__", installed)
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: running)
    assert background.is_outdated() is outdated


# --- a stop ends the process between brains, never in the middle of a sync -------------------------------------------------------
@pytest.mark.windows_unsupported("this test sends a POSIX SIGTERM; Windows stops through a file (MD-181, tested in test_windows_background.py)")
def test_sigterm_lets_the_sync_in_progress_finish_then_the_process_ends(tmp_path):
    marker = tmp_path / "sync-finished"
    child = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from mdpipeline import background, scheduler
        def tick_all(now=None, should_stop=None):
            Path({str(tmp_path / 'sync-started')!r}).write_text("x")
            time.sleep(1.5)                                  # a sync that is running when the signal arrives
            Path({str(marker)!r}).write_text("done")
            return []
        scheduler.tick_all = tick_all
        raise SystemExit(0 if background.run(how="manual") else 3)
    """)
    env = {**os.environ, "MDPIPELINE_DATA_DIR": str(tmp_path / "data"), "MDPIPELINE_NO_BACKGROUND_START": "1"}
    proc = subprocess.Popen([sys.executable, "-c", child], env=env)
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "sync-started").exists():
            assert time.monotonic() < deadline and proc.poll() is None
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert marker.read_text() == "done"               # the running sync was not cut off


# --- a replacement process waits for the lock instead of giving up --------------------------------------------------------------
def test_a_replacement_waits_for_the_lock_and_then_takes_it(monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    fake = FakeBackgroundProcess()
    fake.start()
    time.sleep(0.2)
    assert background.run(max_ticks=1, sleep=lambda s: None, interval=1) is False          # no waiting asked: gives up, as before
    import threading

    threading.Timer(0.6, fake.stop).start()
    assert background.run(max_ticks=1, sleep=lambda s: None, interval=1, wait_for_lock=10) is True


def test_a_replacement_gives_up_when_the_lock_never_frees(monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    fake = FakeBackgroundProcess()
    fake.start()
    time.sleep(0.2)
    try:
        assert background.run(max_ticks=1, sleep=lambda s: None, interval=1, wait_for_lock=0.5) is False
    finally:
        fake.stop()


# --- restart ---------------------------------------------------------------------------------------------------------------------
def test_restart_asks_the_old_process_to_stop_and_starts_a_replacement_that_waits(monkeypatch, started, stops):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.16")        # it handles SIGTERM: it finishes the sync it is on
    assert background.restart() == "restarting"
    assert stops == ["stop"] and started == [("app_start", background.RESTART_WAIT_SECONDS)]


@pytest.mark.parametrize("old", ["0.1.15", "0.1.14", background.UNKNOWN_VERSION])
def test_restart_of_a_process_that_does_not_handle_sigterm_is_reported_as_unsafe(monkeypatch, started, stops, old):
    """Marketing's truth rule (MM-159): "a sync in progress finishes first" is only true for a process with the SIGTERM handler (0.1.16 and later)."""
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: old)
    assert background.restart() == "restarting-unsafe"
    assert stops == ["stop"] and started == [("app_start", background.RESTART_WAIT_SECONDS)]


def test_restart_when_nothing_runs_just_starts_it(monkeypatch, started, stops):
    announce()
    monkeypatch.setattr(background, "is_running", lambda: False)
    assert background.restart() == "started"
    assert stops == [] and started == [("app_start", 0)]


def test_restart_does_nothing_while_background_sync_is_off(monkeypatch, started, stops):
    scheduler.set_background(False)
    assert background.restart() == "off" and stops == [] and started == []


def test_restart_if_outdated_leaves_a_current_process_alone(monkeypatch, started, stops):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "is_outdated", lambda: False)
    assert background.restart_if_outdated() is False and stops == [] and started == []


def test_restart_if_outdated_restarts_an_older_process(monkeypatch, started, stops):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.15")       # old and unsafe: it still counts as restarted
    monkeypatch.setattr(background, "is_outdated", lambda: True)
    assert background.restart_if_outdated() is True and stops == ["stop"] and len(started) == 1


def test_restart_if_outdated_respects_the_opt_out(monkeypatch, started, stops):
    scheduler.set_background(False)
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "is_outdated", lambda: True)
    assert background.restart_if_outdated() is False and stops == [] and started == []


def test_any_command_after_an_update_restarts_an_older_background_process(monkeypatch, started, stops):
    """The path for people whose `update` ran the old code (it cannot restart anything): the first command on the new version heals it."""
    from mdpipeline.schedulestore import SchedulerStore

    with SchedulerStore().update() as data:
        data["background"]["announced"] = True
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "is_outdated", lambda: True)
    assert background.ensure_running() is False                 # nothing new was started by the plain self-heal ...
    assert stops == ["stop"] and len(started) == 1              # ... but the older process was replaced


# --- the commands ---------------------------------------------------------------------------------------------------------------------
def test_schedule_background_restart_says_what_happens(monkeypatch, capsys, started, stops):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.16")
    code, out, _ = run_cli(capsys, "schedule", "background", "restart")
    assert code == 0
    assert out.strip() == (f"Background sync is restarting on version {mdpipeline.__version__}. A sync in progress finishes first.")


def test_schedule_background_restart_of_a_process_that_ends_at_once_does_not_promise_the_sync_finishes(monkeypatch, capsys, started, stops):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.15")
    code, out, _ = run_cli(capsys, "schedule", "background", "restart")
    assert code == 0
    assert out.strip() == (f"Background sync is restarting on version {mdpipeline.__version__}. If a sync was running, it starts again on the next run.")
    assert "finishes first" not in out


def test_schedule_background_restart_says_what_to_do_when_it_did_not_stop(monkeypatch, capsys, started):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "stop", lambda: False)
    _, out, _ = run_cli(capsys, "schedule", "background", "restart")
    assert out.strip() == ("Background sync didn't stop. End the mdpipeline process, then run: mdpipeline schedule background restart")
    assert "Log out" not in out and "mdpipeline background" not in out


def test_restart_does_not_start_background_sync_for_someone_it_was_never_turned_on_for(monkeypatch, capsys, started, stops):
    monkeypatch.setattr(background, "is_running", lambda: False)
    _, out, _ = run_cli(capsys, "schedule", "background", "restart")
    assert started == [] and out.strip() == "Background sync is not set up yet. It turns on after your first successful sync."


def test_schedule_background_restart_when_it_was_not_running(monkeypatch, capsys, started, stops):
    announce()
    monkeypatch.setattr(background, "is_running", lambda: False)
    _, out, _ = run_cli(capsys, "schedule", "background", "restart")
    assert out.strip() == f"Background sync was not running. Starting it now on version {mdpipeline.__version__}."


def test_schedule_background_restart_when_it_is_off(capsys, started, stops):
    scheduler.set_background(False)
    _, out, _ = run_cli(capsys, "schedule", "background", "restart")
    assert out.strip() == "Background sync is off. Turn it on with: mdpipeline schedule background on" and started == []


def test_schedule_background_off_really_stops_the_running_process(monkeypatch, capsys, stops):
    run_cli(capsys, "schedule", "background", "off")
    assert stops == ["stop"] and not scheduler.background_enabled()


def test_the_ui_switch_off_also_stops_the_process(stops):
    from mdpipeline import uiapi

    uiapi.set_background_view(False)
    assert stops == ["stop"]


def test_turning_it_on_again_while_the_old_one_still_finishes_starts_a_process_that_waits_for_it(monkeypatch, started):
    scheduler.set_background(False)
    monkeypatch.setattr(background, "is_running", lambda: True)
    scheduler.turn_on_background()
    assert started and started[-1][1] == background.RESTART_WAIT_SECONDS


def test_turning_it_on_when_nothing_runs_starts_one_that_does_not_wait(started):
    scheduler.set_background(False)
    scheduler.turn_on_background()
    assert started and started[-1][1] == 0


# --- update -------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def update_ok(monkeypatch):
    from mdpipeline import install_info, lifecycle

    monkeypatch.setattr(install_info, "install_method", lambda: "uv-tool")
    monkeypatch.setattr(lifecycle, "update_command", lambda method: ["uv", "tool", "upgrade", "mdpipeline"])
    monkeypatch.setattr(lifecycle, "run_command", lambda command: 0)
    ran = []

    def fake_run(argv, **kw):
        if "-c" in argv:                                    # the new interpreter, asked which version it is
            return subprocess.CompletedProcess(argv, 0, stdout="0.1.16\n")
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return ran


def test_update_restarts_the_background_process_through_the_newly_installed_code(update_ok, capsys):
    code, out, _ = run_cli(capsys, "update")
    assert code == 0
    assert update_ok == [[sys.executable, "-m", "mdpipeline", "schedule", "background", "restart"]]       # a new interpreter: the new code answers
    assert "Updated to version 0.1.16. Your configuration, vault and files are unchanged." in out.splitlines()


def test_update_never_tells_the_person_to_do_off_then_on(update_ok, capsys):
    _, out, _ = run_cli(capsys, "update")
    assert "off, then on" not in out and "next login" not in out


def test_a_failed_update_does_not_touch_the_background_process(monkeypatch, capsys):
    from mdpipeline import install_info, lifecycle

    monkeypatch.setattr(install_info, "install_method", lambda: "uv-tool")
    monkeypatch.setattr(lifecycle, "update_command", lambda method: ["uv", "tool", "upgrade", "mdpipeline"])
    monkeypatch.setattr(lifecycle, "run_command", lambda command: 1)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: ran.append(argv))
    code, _, err = run_cli(capsys, "update")
    assert code != 0 and ran == [] and "not updated" in err


def test_if_the_restart_step_cannot_run_the_update_still_succeeds_and_says_how_to_restart(monkeypatch, update_ok, capsys):
    def boom(argv, **kw):
        raise OSError("no interpreter")

    monkeypatch.setattr(subprocess, "run", boom)
    code, out, _ = run_cli(capsys, "update")
    assert code == 0 and "Background sync may still be running the old version. To restart it, run: mdpipeline schedule background restart" in out
    assert "Updated. Your configuration" in out or "Updated to version" in out


# --- status and schedule show -----------------------------------------------------------------------------------------------------------
def test_schedule_show_names_the_running_version(monkeypatch, capsys):
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: mdpipeline.__version__)
    _, out, _ = run_cli(capsys, "schedule", "show")
    assert f"Background sync: on (version {mdpipeline.__version__}" in out


def test_schedule_show_says_when_the_running_version_is_older_and_gives_one_next_step(monkeypatch, capsys):
    monkeypatch.setattr(mdpipeline, "__version__", "0.1.16")
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.15")
    _, out, _ = run_cli(capsys, "schedule", "show")
    assert ("Background sync: on, but it is still running the older version 0.1.15 (installed: 0.1.16). "
            "Restart it: mdpipeline schedule background restart") in out


def test_schedule_show_for_a_process_of_unknown_age(monkeypatch, capsys):
    monkeypatch.setattr(mdpipeline, "__version__", "0.1.16")
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: background.UNKNOWN_VERSION)
    _, out, _ = run_cli(capsys, "schedule", "show")
    assert "still running an older version (installed: 0.1.16). Restart it: mdpipeline schedule background restart" in out


def test_the_ui_background_view_carries_the_running_version_and_whether_it_is_outdated(monkeypatch):
    from mdpipeline import uiapi

    monkeypatch.setattr(mdpipeline, "__version__", "0.1.16")
    monkeypatch.setattr(background, "is_running", lambda: True)
    monkeypatch.setattr(background, "running_version", lambda: "0.1.15")
    view = uiapi.background_view()
    assert view["version"] == "0.1.15" and view["installed_version"] == "0.1.16" and view["outdated"] is True


def test_the_ui_background_view_is_quiet_when_nothing_runs(monkeypatch):
    from mdpipeline import uiapi

    monkeypatch.setattr(background, "is_running", lambda: False)
    view = uiapi.background_view()
    assert view["outdated"] is False and view["version"] is None
