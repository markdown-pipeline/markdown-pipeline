"""MD-117 AC2: one command opens the UI on a clean machine, with a default brain made for the person."""
from pathlib import Path

import pytest

from mdpipeline import cli, ui_server
from mdpipeline.brain import init_brain


class FakeServer:
    url = "http://127.0.0.1:8765/"

    def __init__(self, brain, port):
        FakeServer.brain, FakeServer.port = brain, port

    def serve_forever(self):
        FakeServer.served = True

    def server_close(self):
        pass


@pytest.fixture
def served(monkeypatch):
    opened = []
    monkeypatch.setattr(ui_server, "make_server", lambda brain, port=8765: FakeServer(brain, port))
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    FakeServer.served = False
    return opened


def test_on_a_clean_machine_start_makes_the_default_brain_and_opens_the_ui(served, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["start"]) == 0
    home = Path.home() / "markdown-pipeline"
    assert not (home / "raw").exists() and (home / ".mdpipeline" / "config.yaml").is_file()
    assert FakeServer.brain.root == home.resolve() and FakeServer.served and served == ["http://127.0.0.1:8765/"]
    out = capsys.readouterr().out
    assert "markdown-pipeline" in out and "Ctrl+C" in out


def test_a_second_start_reuses_the_brain_and_changes_nothing(served, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["start"])
    config = (Path.home() / "markdown-pipeline/.mdpipeline/config.yaml").read_text(encoding="utf-8")
    cli.main(["start"])
    assert (Path.home() / "markdown-pipeline/.mdpipeline/config.yaml").read_text(encoding="utf-8") == config


def test_a_registered_brain_at_or_above_the_current_directory_wins_over_the_default(served, brain, monkeypatch):
    monkeypatch.chdir(brain.root)
    cli.main(["start"])
    assert FakeServer.brain.root == brain.root and not (Path.home() / "markdown-pipeline").exists()


def test_brain_flag_wins(served, brain, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["--brain", str(brain.root), "start"])
    assert FakeServer.brain.root == brain.root


def test_no_browser_only_prints_the_url(served, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["start", "--no-browser"])
    assert served == [] and "http://127.0.0.1:8765/" in capsys.readouterr().out


def test_typing_just_the_command_is_start(served, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 0 and FakeServer.served


def test_start_does_not_ask_for_the_usage_notice_in_the_terminal_before_the_ui_can_ask(served, tmp_path, monkeypatch):
    assert cli.needs_usage_notice("start") is False
