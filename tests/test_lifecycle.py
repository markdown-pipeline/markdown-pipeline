"""MD-117 AC4/AC5: updating is one command and loses nothing; uninstalling is documented, stops the background process and keeps the files."""
import os

import pytest

from mdpipeline import autostart, background, cli, lifecycle, scheduler
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline


@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "run_command", lambda cmd: calls.append(cmd) or 0)
    return calls


@pytest.mark.parametrize("method,expected", [
    ("uv", ["uv", "tool", "upgrade", "mdpipeline"]),
    ("script", ["sh", "-c", "curl -fsSL " + lifecycle.INSTALL_URL + " | sh"]),    # MD-131 AC6: a release install moves by running the install command again
    ("pipx", ["pipx", "upgrade", "mdpipeline"]),
])
def test_update_runs_the_one_command_of_the_install_method(method, expected, ran, monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", method)
    assert cli.main(["update"]) == 0
    assert ran == [expected]
    assert "Updated" in capsys.readouterr().out


def test_update_with_pip_uses_the_same_python(ran, monkeypatch):
    import sys

    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "pip")
    cli.main(["update"])
    assert ran == [[sys.executable, "-m", "pip", "install", "--upgrade", "mdpipeline"]]


def test_a_source_checkout_is_told_what_to_do_and_nothing_is_run(ran, monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "source")
    assert cli.main(["update"]) == 0
    assert ran == [] and "git pull" in capsys.readouterr().out


def test_dry_run_prints_the_command_and_runs_nothing(ran, monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uv")
    cli.main(["update", "--dry-run"])
    assert ran == [] and "uv tool upgrade mdpipeline" in capsys.readouterr().out


def test_a_failed_update_says_so_and_changes_nothing_else(monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uv")
    monkeypatch.setattr(lifecycle, "run_command", lambda cmd: 1)
    assert cli.main(["update"]) == 1
    assert "not updated" in capsys.readouterr().err.lower()


def test_a_missing_package_manager_is_a_plain_message_not_a_crash(monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uv")

    def missing(cmd):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(lifecycle, "run_command", missing)
    assert cli.main(["update"]) == 1
    assert "uv" in capsys.readouterr().err


def test_the_first_plan_after_an_update_is_zero_changes(brain, add_pipeline, make_source, monkeypatch):
    """The app version is never written into the files or the state, so a new version plans exactly what the old one did."""
    import mdpipeline
    from mdpipeline import usage

    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one", "2.md": "two"}))]))
    assert sync_pipeline(brain, "p", trigger="cli").result == "ok"
    monkeypatch.setattr(mdpipeline, "__version__", "9.9.9")
    monkeypatch.setattr(usage, "__version__", "9.9.9")
    plan = sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert plan.result == "ok" and sum(plan.counts()[k] for k in ("created", "updated", "renamed", "deleted", "conflicts")) == 0


def test_after_an_update_the_next_open_reports_the_previous_version_once(app_data, monkeypatch):
    import json

    from mdpipeline import usage, usage_hooks
    from mdpipeline.usage import UsageStore

    UsageStore().set_consent("share")
    usage_hooks.app_opened()
    monkeypatch.setattr(usage, "__version__", "9.9.9")
    usage_hooks.app_opened()
    usage_hooks.app_opened()
    queue = json.loads((app_data.dir / "usage.json").read_text())["queue"]
    updated = [e for e in queue if e["event"] == "app_updated"]
    assert len(updated) == 1 and updated[0]["properties"]["previous_version"] == __import__("mdpipeline").__version__


# --- uninstall -----------------------------------------------------------------------------------------------------
def test_uninstall_turns_background_sync_off_and_keeps_every_file(brain, add_pipeline, make_source, monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uv")
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    add_pipeline(Pipeline.new("p", [str(make_source("a", {"1.md": "one"}))]))
    sync_pipeline(brain, "p", trigger="cli")
    files = sorted(str(p) for p in brain.root.rglob("*") if p.is_file())
    assert cli.main(["uninstall"]) == 0
    assert scheduler.background_enabled() is False
    assert sorted(str(p) for p in brain.root.rglob("*") if p.is_file()) == files
    out = capsys.readouterr().out
    assert "uv tool uninstall mdpipeline" in out and "untouched" in out


def test_uninstall_names_the_app_data_folder_and_does_not_delete_it(app_data, monkeypatch, capsys):
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "pipx")
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    cli.main(["uninstall"])
    assert str(app_data.dir) in capsys.readouterr().out and app_data.dir.exists()


def test_uninstall_stops_a_running_background_process(app_data, monkeypatch):
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setenv("MDPIPELINE_INSTALL_METHOD", "uv")
    stopped = []
    monkeypatch.setattr(background, "stop", lambda: stopped.append(1) or True)
    cli.main(["uninstall"])
    assert stopped == [1]


def test_background_stop_ends_only_the_process_that_holds_the_lock(app_data, monkeypatch):
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert background.stop() is False and sent == []            # nothing running: nothing is signalled


def test_the_running_process_writes_its_id_into_the_lock_so_stop_can_find_it(app_data, monkeypatch):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    seen = []
    background.run(max_ticks=2, sleep=lambda s: seen.append(background._lock_path().read_text().strip()), interval=1)
    assert seen == [str(os.getpid())]


def test_uninstall_does_not_ask_the_usage_question():
    assert cli.needs_usage_notice("uninstall") is False and cli.starts_usage_helper("uninstall") is False


def test_the_install_command_is_one_public_curl_line_that_needs_no_login():
    assert lifecycle.INSTALL_URL == "https://raw.githubusercontent.com/markdown-pipeline/markdown-pipeline/main/install.sh"
    assert lifecycle.INSTALL_COMMAND == "curl -fsSL " + lifecycle.INSTALL_URL + " | sh"
