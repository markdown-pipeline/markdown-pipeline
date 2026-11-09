"""MD-183 (found on the real Windows VM, MD-184): `mdpipeline update` on Windows must not replace the program from inside itself.

A running program's files are locked on Windows: `uv tool install --force` run by the app's own python.exe failed with "Access is denied" and left a BROKEN
install. So on Windows `update` hands the work to a small detached PowerShell helper and exits. The helper waits until nothing runs from the tool folder
(the background process is asked to stop first), runs the installer, then restarts background sync. If something still holds the folder after the wait
it changes NOTHING and says so in a log file: never a half-removed install.

Three kinds of test: `update` itself (what it starts, what it prints), the text of the helper script, and the script RUN under PowerShell 7 in a container
(a Linux container: the locked exe itself is for the Windows VM gate, MD-184)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mdpipeline import background, cli, lifecycle, winupdate

APP = Path(__file__).resolve().parent.parent
IMAGE = "mcr.microsoft.com/powershell:ubuntu-24.04"
INSTALLER = ["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-Command", "$env:MDPIPELINE_NO_OPEN='1'; irm X | iex; exit $LASTEXITCODE"]


# --- what `update` does on Windows -------------------------------------------------------------------------------------------
@pytest.fixture
def windows(monkeypatch, tmp_path):
    """Windows, a script install; nothing is really run: the installer and the helper are recorded."""
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "script")
    monkeypatch.setattr(lifecycle.sys, "platform", "win32")
    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(tmp_path / "data"))
    calls = {"installer": [], "helper": []}
    monkeypatch.setattr(lifecycle, "run_command", lambda cmd: calls["installer"].append(cmd) or 0)
    log = tmp_path / "data" / "update.log"
    monkeypatch.setattr(winupdate, "start", lambda command: calls["helper"].append(command) or log)
    calls["log"] = log
    return calls


def test_update_on_windows_never_runs_the_installer_from_inside_the_app(windows):
    assert cli.main(["update"]) == 0
    assert windows["installer"] == []


def test_update_on_windows_hands_the_installer_command_to_the_helper(windows):
    cli.main(["update"])
    (command,) = windows["helper"]
    assert command[:5] == ["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-Command"]
    assert "MDPIPELINE_NO_OPEN='1'" in command[5] and lifecycle.INSTALL_PS1_URL in command[5]


def test_update_on_windows_says_it_started_and_where_the_log_is_and_does_not_claim_it_is_done(windows, capsys):
    cli.main(["update"])
    out = capsys.readouterr().out
    assert "Update started. It is not finished yet: it takes about a minute, and begins once this program has closed." in out
    assert 'Close any other Markdown Pipeline window (including "mdpipeline start")' in out and "after 2 minutes without changing anything." in out
    assert f'The log says "Updated." when it is done: {windows["log"]}' in out
    assert "Background sync, if it is on, pauses and restarts by itself." in out
    assert "Check with:" not in out                       # MM review: `schedule` shows sync state, not whether the update worked
    assert "Updated" not in out.replace('says "Updated."', "")


def test_a_windows_dry_run_starts_nothing(windows, capsys):
    assert cli.main(["update", "--dry-run"]) == 0
    assert windows["helper"] == [] and windows["installer"] == [] and "Would run" in capsys.readouterr().out


def test_if_the_helper_cannot_be_started_update_fails_and_changes_nothing(monkeypatch, windows, capsys):
    def boom(command):
        raise OSError("no powershell")
    monkeypatch.setattr(winupdate, "start", boom)
    assert cli.main(["update"]) == 1
    assert "not updated" in capsys.readouterr().err.lower()


def test_other_systems_still_update_in_place(monkeypatch):
    ran = []
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "script")
    monkeypatch.setattr(lifecycle.sys, "platform", "linux")
    monkeypatch.setattr(lifecycle, "run_command", lambda cmd: ran.append(cmd) or 0)
    monkeypatch.setattr(winupdate, "start", lambda command: pytest.fail("the helper is Windows only"))
    cli.main(["update"])
    assert ran == [["sh", "-c", lifecycle.INSTALL_COMMAND]]


def test_a_package_cache_clean_is_not_run_through_the_helper(monkeypatch, windows):
    """`uvx` installs keep nothing on disk that is running: only the cache is cleared, no folder is replaced."""
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uvx")
    cli.main(["update"])
    assert windows["helper"] == [] and windows["installer"] == [["uv", "cache", "clean", "mdpipeline"]]


# --- starting the helper ------------------------------------------------------------------------------------------------------
def test_start_writes_the_script_runs_it_detached_and_asks_the_background_process_to_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(tmp_path))
    spawned, stopped = [], []
    monkeypatch.setattr(winupdate.subprocess, "Popen", lambda args, **kw: spawned.append((args, kw)))
    monkeypatch.setattr(background, "stop", lambda: stopped.append(True) or True)
    log = winupdate.start(INSTALLER)
    (args, kw), = spawned
    assert args[:5] == ["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-File"]
    script = Path(args[5])
    assert script.parent == tmp_path and script.read_text(encoding="utf-8").startswith("#")
    assert log == tmp_path / "update.log" and stopped == [True]
    assert kw.get("stdin") == subprocess.DEVNULL and kw.get("creationflags") is not None or "start_new_session" in kw     # detached either way


def test_start_does_not_stop_background_sync_when_the_helper_could_not_start(monkeypatch, tmp_path):
    monkeypatch.setenv("MDPIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(winupdate.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(background, "stop", lambda: pytest.fail("nothing may be stopped when nothing will restart it"))
    with pytest.raises(OSError):
        winupdate.start(INSTALLER)


# --- the text of the helper script ----------------------------------------------------------------------------------------------
def script(**kw):
    args = dict(command=INSTALLER, watch=[r"C:\Users\a\AppData\Roaming\uv\tools\mdpipeline", r"C:\Users\a\.local\bin\mdpipeline.exe"],
                log=r"C:\Users\a\AppData\Local\mdpipeline\update.log", restart=[r"C:\Users\a\.local\bin\mdpipeline.exe", "schedule", "background", "restart"],
                wait_seconds=120)
    args.update(kw)
    return winupdate.helper_script(**args)


def test_the_script_is_plain_ascii_for_windows_powershell_5_1():
    assert script().isascii()


def test_the_script_waits_the_given_number_of_seconds_and_names_what_it_watches():
    text = script(wait_seconds=77)
    assert "$Wait = 77" in text and r"C:\Users\a\AppData\Roaming\uv\tools\mdpipeline" in text and r"C:\Users\a\.local\bin\mdpipeline.exe" in text


def test_quotes_in_a_path_or_argument_cannot_break_out_of_the_script():
    text = script(command=["it's", "a b", "$(evil)"], log=r"C:\Users\o'neil\update.log")
    assert "'it''s'" in text and "'a b'" in text and "'$(evil)'" in text and r"'C:\Users\o''neil\update.log'" in text


def test_the_script_says_nothing_was_changed_when_something_still_holds_the_folder():
    assert "Not updated: Markdown Pipeline was still running after 2 minutes, so nothing was changed." in script()
    assert 'Close it (including any open "mdpipeline start" window), then run: mdpipeline update' in script()


# --- the script RUN under PowerShell 7 -----------------------------------------------------------------------------------------
def _docker_ready() -> str | None:
    if not shutil.which("docker"):
        return "docker is not installed"
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        return f"the image {IMAGE} is not pulled (docker pull {IMAGE})"
    return None


needs_pwsh = pytest.mark.skipif(_docker_ready() is not None, reason=_docker_ready() or "")


def run_helper(tmp_path: Path, *, install_exit: int = 0, hold_folder: bool = False, wait_seconds: int = 3):
    """Run the helper in a container with stand-in commands; return (exit code, log text, which stand-ins ran)."""
    tool = tmp_path / "tool"
    (tool / "Scripts").mkdir(parents=True)
    ran = tmp_path / "ran.txt"
    installer = tmp_path / "installer.sh"
    installer.write_text(f"#!/bin/sh\necho installer >> /w/ran.txt\nexit {install_exit}\n")
    restart = tmp_path / "restart.sh"
    restart.write_text("#!/bin/sh\necho restart >> /w/ran.txt\n")
    for f in (installer, restart):
        f.chmod(0o755)
    text = winupdate.helper_script(command=["/w/installer.sh"], watch=["/w/tool"], log="/w/update.log", restart=["/w/restart.sh"], wait_seconds=wait_seconds)
    (tmp_path / "helper.ps1").write_text(text, encoding="utf-8")
    holder = ""
    if hold_folder:           # a process whose program file lives inside the watched folder, as the app's python.exe does on Windows
        shutil.copy(shutil.which("sleep"), tool / "Scripts" / "python")
        holder = "/w/tool/Scripts/python 60 & sleep 0.5; "
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-v", f"{tmp_path}:/w", IMAGE, "bash", "-c", f"{holder}pwsh -NoProfile -File /w/helper.ps1; echo exit=$?"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    code = int(re.search(r"exit=(\d+)", out).group(1))
    log = (tmp_path / "update.log").read_text() if (tmp_path / "update.log").exists() else ""
    return code, log, ran.read_text().split() if ran.exists() else []


@needs_pwsh
def test_run_free_folder_installs_then_restarts_background_sync(tmp_path):
    code, log, ran = run_helper(tmp_path)
    assert code == 0 and ran == ["installer", "restart"] and "Updated." in log


@needs_pwsh
def test_run_a_folder_still_in_use_changes_nothing_and_says_so(tmp_path):
    code, log, ran = run_helper(tmp_path, hold_folder=True)
    assert code == 1 and "installer" not in ran
    assert "Not updated: Markdown Pipeline was still running after 2 minutes, so nothing was changed." in log


@needs_pwsh
def test_run_a_failing_installer_is_logged_and_background_sync_is_still_brought_back(tmp_path):
    code, log, ran = run_helper(tmp_path, install_exit=3)
    assert code == 1 and ran == ["installer", "restart"] and "The update failed (installer exit 3). Markdown Pipeline may not start now. To repair it, run: " in log


@needs_pwsh
def test_run_a_stuck_folder_still_restarts_background_sync_it_had_stopped(tmp_path):
    """The app asked background sync to stop before handing over: whatever happens, it is asked to come back."""
    code, log, ran = run_helper(tmp_path, hold_folder=True)
    assert ran == ["restart"]


def test_the_restart_failure_line_uses_the_reviewed_words():
    assert "Could not restart background sync. If you use it, run: mdpipeline schedule background restart" in script()
