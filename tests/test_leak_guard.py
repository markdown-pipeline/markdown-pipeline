"""MD-169 AC4/AC5: the leak guard itself. A test that leaves an `mdpipeline background` process running, or that changes the real
user's scheduler registry or login items, must be caught (the guard lives in tests/leakguard.py and is wired in conftest.py)."""
import json
import os
import signal
import subprocess
import sys
import time

import leakguard


def _spawn_fake_background():
    """A real process whose command line looks like `<venv python> ... -m mdpipeline background` but only sleeps."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "-m", "mdpipeline", "background", "--started-by", "app_start"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _wait_for(predicate, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_background_process_started_after_the_baseline_is_reported_as_a_leak():
    before = leakguard.background_pids()
    proc = _spawn_fake_background()
    try:
        assert _wait_for(lambda: proc.pid in leakguard.leaked_since(before))
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_a_background_process_already_running_at_the_baseline_is_not_a_leak():
    proc = _spawn_fake_background()
    try:
        assert _wait_for(lambda: proc.pid in leakguard.background_pids())
        before = leakguard.background_pids()
        assert proc.pid not in leakguard.leaked_since(before)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_a_background_process_from_another_python_is_not_ours_to_judge(tmp_path):
    """The owner's installed app runs from ~/.local/share/uv/tools/...: it is neither a leak nor ours to kill."""
    other = tmp_path / "other-python"
    other.symlink_to(sys.executable)
    proc = subprocess.Popen([str(other), "-c", "import time; time.sleep(60)", "-m", "mdpipeline", "background"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        before = set()
        time.sleep(0.3)
        assert proc.pid not in leakguard.leaked_since(before)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_kill_stops_the_leaked_processes():
    proc = _spawn_fake_background()
    assert _wait_for(lambda: proc.pid in leakguard.background_pids())
    leakguard.kill([proc.pid])
    assert proc.wait(timeout=5) is not None


def _fake_home(tmp_path):
    data = tmp_path / ".local/share/mdpipeline"
    data.mkdir(parents=True)
    (data / "scheduler.json").write_text(json.dumps({"background": {"enabled": True}, "brains": ["/real/brain"], "runtime": {"/real/brain": {"last_tick": 1}}}))
    autostart = tmp_path / ".config/autostart"
    autostart.mkdir(parents=True)
    (autostart / "mdpipeline-background.desktop").write_text("Exec=old\n")
    return tmp_path


def test_real_state_unchanged_reports_nothing(tmp_path):
    home = _fake_home(tmp_path)
    assert leakguard.real_state_changes(leakguard.real_state(home), leakguard.real_state(home)) == []


def test_runtime_bookkeeping_of_a_real_running_app_is_not_a_change(tmp_path):
    """The owner's installed app ticks every minute and rewrites `runtime`: that is not a test writing to the real home."""
    home = _fake_home(tmp_path)
    before = leakguard.real_state(home)
    path = home / ".local/share/mdpipeline/scheduler.json"
    data = json.loads(path.read_text())
    data["runtime"]["/real/brain"]["last_tick"] = 999
    path.write_text(json.dumps(data))
    assert leakguard.real_state_changes(before, leakguard.real_state(home)) == []


def test_a_brain_added_to_the_real_scheduler_is_a_change(tmp_path):
    home = _fake_home(tmp_path)
    before = leakguard.real_state(home)
    path = home / ".local/share/mdpipeline/scheduler.json"
    data = json.loads(path.read_text())
    data["brains"].append("/tmp/tmpabc/brain")
    path.write_text(json.dumps(data))
    changes = leakguard.real_state_changes(before, leakguard.real_state(home))
    assert any("/tmp/tmpabc/brain" in c for c in changes)


def test_a_new_login_item_in_the_real_home_is_a_change(tmp_path):
    home = _fake_home(tmp_path)
    before = leakguard.real_state(home)
    (home / ".config/autostart/other.desktop").write_text("x")
    assert any("other.desktop" in c for c in leakguard.real_state_changes(before, leakguard.real_state(home)))


def test_a_rewritten_login_item_is_a_change(tmp_path):
    home = _fake_home(tmp_path)
    before = leakguard.real_state(home)
    (home / ".config/autostart/mdpipeline-background.desktop").write_text("Exec=new\n")
    assert any("mdpipeline-background.desktop" in c for c in leakguard.real_state_changes(before, leakguard.real_state(home)))


def test_a_new_file_in_the_real_data_dir_is_a_change(tmp_path):
    home = _fake_home(tmp_path)
    before = leakguard.real_state(home)
    (home / ".local/share/mdpipeline/usage.json").write_text("{}")
    assert any("usage.json" in c for c in leakguard.real_state_changes(before, leakguard.real_state(home)))


def test_a_transient_temp_file_of_a_running_installed_app_is_not_a_change(tmp_path):
    """The owner's installed app saves scheduler.json atomically through `scheduler.<pid>.<thread>.tmp`: seen by the guard at the wrong
    moment it failed an unrelated test (2026-10-21 full run). Only lasting files count."""
    home = _fake_home(tmp_path)
    before = leakguard.real_state(home)
    (home / ".local/share/mdpipeline/scheduler.2254.140428739087232.tmp").write_text("{}")
    assert leakguard.real_state_changes(before, leakguard.real_state(home)) == []


def test_a_missing_real_home_state_reads_as_empty_not_as_an_error(tmp_path):
    state = leakguard.real_state(tmp_path)
    assert leakguard.real_state_changes(state, leakguard.real_state(tmp_path)) == []
