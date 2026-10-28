"""MD-166: logging out over SSH must not silently stop background sync. Where the OS cannot keep it running without a privileged command
(linger off), the user is told once, where they are: the first-run Ready step, Home, and the CLI output. The app never runs it.

AC1/AC2 (the mechanism is AC2: nothing outside the user manager survives a logout on every distro, so the hint is the guarantee);
AC3 CLI status after a self-start; AC4 no retired term "brain" in CLI messages."""
import ast
import re
from pathlib import Path

import pytest

from mdpipeline import autostart, background, cli, scheduler
from mdpipeline.schedulestore import SchedulerStore
from test_background_headless import FakeSystemd, turn_on

PKG = Path(__file__).parent.parent / "src" / "mdpipeline"


@pytest.fixture
def headless(app_data, monkeypatch):
    """A server over SSH: systemd works, linger is off, and there is no desktop session."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    fake = FakeSystemd(linger="no")
    monkeypatch.setattr(autostart, "_run", fake)
    return fake


# --- AC2: what the line says ------------------------------------------------------------------------------------------
def test_the_line_says_what_happens_without_linger_and_gives_both_commands():
    line = autostart.LINGER_LINE
    assert "stops when you log out" in line and "after a reboot it waits until you log in" in line
    assert "loginctl enable-linger $USER" in line and "sudo loginctl enable-linger $USER" in line and "access denied" in line


def test_the_notice_is_for_a_box_with_no_desktop_session_where_linger_is_off(headless):
    assert autostart.linger_notice() is True


def test_no_notice_on_a_desktop_session(headless, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    assert autostart.linger_notice() is False


def test_no_notice_when_linger_is_on(app_data, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="yes"))
    assert autostart.linger_notice() is False


def test_the_view_carries_the_notice_and_the_text_only_when_background_is_on(headless):
    from mdpipeline.uiapi import background_view

    view = background_view()
    assert view["linger_notice"] is True and view["linger_text"] == autostart.LINGER_LINE
    scheduler.set_background(False)
    assert background_view()["linger_notice"] is False


def test_hiding_the_notice_on_home_is_remembered_until_linger_is_on(headless):
    from mdpipeline.uiapi import background_view

    scheduler.hide_linger_notice()
    assert background_view()["linger_notice"] is False and background_view()["linger_hint"] is True      # Settings keeps the line


def test_the_hide_button_has_an_endpoint(headless, brain):
    from uihelper import running_ui

    with running_ui(brain) as ui:
        assert ui.get("/api/state")[1]["background"]["linger_notice"] is True
        assert ui.post("/api/background/linger-hide", {})[0] == 200
        assert ui.get("/api/state")[1]["background"]["linger_notice"] is False


def test_the_app_never_runs_enable_linger_even_with_the_notice(headless, tmp_path):
    from mdpipeline.uiapi import background_view

    autostart.enable_user_service(home=tmp_path, python="/p")
    background_view()
    assert not any("enable-linger" in " ".join(c) for c in headless.calls) and not any(c[0] == "sudo" for c in headless.calls)


# --- AC2: the CLI output ----------------------------------------------------------------------------------------------
def test_schedule_background_on_prints_the_line(headless, brain, capsys):
    assert cli.main(["--brain", str(brain.root), "schedule", "background", "on"]) == 0
    assert autostart.LINGER_LINE_PLAIN in capsys.readouterr().out


def test_schedule_show_prints_the_line_when_background_is_on(headless, brain, capsys):
    turn_on()
    assert cli.main(["--brain", str(brain.root), "schedule"]) == 0
    assert autostart.LINGER_LINE_PLAIN in capsys.readouterr().out


def test_schedule_show_prints_nothing_extra_when_linger_is_on(app_data, brain, monkeypatch, capsys):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="yes"))
    turn_on()
    cli.main(["--brain", str(brain.root), "schedule"])
    assert "enable-linger" not in capsys.readouterr().out


def test_schedule_background_off_prints_no_line(headless, app_data, capsys):
    cli.main(["schedule", "background", "off"])
    assert "enable-linger" not in capsys.readouterr().out


# --- AC3: status right after a self-start never says "not running" -----------------------------------------------------
def test_status_after_a_self_start_says_starting_never_not_running(app_data, brain, monkeypatch, capsys):
    turn_on()
    monkeypatch.setattr(autostart, "start_now", lambda python=None, how="app_start": True)
    monkeypatch.setattr(background, "STARTUP_WAIT_SECONDS", 0)
    assert cli.main(["--brain", str(brain.root), "schedule"]) == 0
    out = capsys.readouterr().out
    assert "not running" not in out and "Background sync: on (starting now)" in out


def test_status_waits_a_moment_for_the_self_started_process_and_then_says_on(app_data, brain, monkeypatch, capsys):
    turn_on()
    seen = iter([False, False, True, True, True])
    monkeypatch.setattr(autostart, "start_now", lambda python=None, how="app_start": True)
    monkeypatch.setattr(background, "is_running", lambda: next(seen, True))
    monkeypatch.setattr(background, "STARTUP_POLL_SECONDS", 0)
    cli.main(["--brain", str(brain.root), "schedule"])
    out = capsys.readouterr().out
    assert "Background sync: on" in out and "not running" not in out


def test_status_still_says_not_running_when_nothing_was_started_and_nothing_runs(app_data, brain, monkeypatch, capsys):
    turn_on()
    monkeypatch.setattr(autostart, "start_now", lambda python=None, how="app_start": False)       # the start failed
    cli.main(["--brain", str(brain.root), "schedule"])
    assert "on but not running" in capsys.readouterr().out


# --- AC4: no "brain" in CLI messages ----------------------------------------------------------------------------------
def test_the_no_output_folder_error_uses_the_agreed_term(tmp_path):
    from mdpipeline.brain import BrainError, find_brain

    with pytest.raises(BrainError) as exc:
        find_brain(tmp_path)
    text = str(exc.value)
    assert "brain" not in text.lower() and "output folder" in text and "mdpipeline init" in text


SPOKEN_FUNCTIONS = {"missing_pipeline_message"}       # build the sentence the person reads
SPOKEN = {"print", "err", "UsageError", "BrainError", "ValueError", "RuntimeError"}


def spoken_strings(path: Path):
    """String parts of print(...)/err(...)/raise XError(...) calls: what the person reads at run time. Flags (--brain) are not prose."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in SPOKEN_FUNCTIONS:
            for part in ast.walk(node):
                if isinstance(part, ast.Constant) and isinstance(part.value, str) and not (node.body and part is getattr(node.body[0], "value", None)):
                    yield part.lineno, part.value
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in SPOKEN:
                for part in ast.walk(node):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        yield part.lineno, part.value


def test_no_cli_message_says_brain():
    found = []
    for name in ("cli.py", "brain.py"):
        for number, text in spoken_strings(PKG / name):
            if re.search(r"(?<![-\w])brains?\b|\bBrain\b", text) and not re.fullmatch(r"\W*--brain\W*", text):
                found.append(f"{name}:{number}: {text[:90]!r}")
    assert found == []


# --- AC2: where the line appears in the app ---------------------------------------------------------------------------
def test_home_and_the_ready_step_render_the_line_from_the_state():
    js = (PKG / "ui_static/app.js").read_text()
    fr = (PKG / "ui_static/firstrun.js").read_text()
    assert "renderLingerNotice" in js and "linger_notice" in js and "linger-hide" in js
    assert "linger_notice" in fr
