"""MD-115 (FR-S1 AC3, AC5, AC6): `mdpipeline usage`, the ask-once prompt of the first interactive run, the hint for non-interactive runs."""
from __future__ import annotations

import builtins
import json

import pytest

from mdpipeline import cli, usage
from mdpipeline.cli import main
from mdpipeline.usage import UsageStore

CHEAP = ["github-app", "show"]          # a command that needs no brain: used to trigger the first-run notice


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


@pytest.fixture(autouse=True)
def fresh_notices(app_data):
    """conftest marks the notices as seen so other tests stay quiet; these tests need the real first-run state."""
    path = app_data.dir / "usage.json"
    if path.exists():
        path.unlink()


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def interactive(monkeypatch, *answers: str):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr(cli, "stderr_visible", lambda: True)
    given = iter(answers)

    def fake_input(prompt=""):
        try:
            return next(given)
        except StopIteration:
            raise AssertionError("asked again after the one-time question") from None

    monkeypatch.setattr(builtins, "input", fake_input)


# --- the usage command -----------------------------------------------------------------------------------------------
def test_status_for_a_new_install_says_nothing_is_shared_and_lists_what_is_and_is_never_sent(capsys):
    code, out, err = run(capsys, "usage", "status")
    assert code == 0, err
    assert "not chosen" in out.lower() and "nothing is shared" in out.lower()
    for line in usage.WHAT_WE_SEND + usage.NEVER_SENT:
        assert line in out
    assert UsageStore().install_id in out


def test_status_also_discloses_what_every_event_carries(capsys):
    _, out, _ = run(capsys, "usage", "status")
    assert usage.ALWAYS_SENT in out


def test_on_turns_sharing_on_and_off_turns_it_off_immediately(capsys):
    code, out, _ = run(capsys, "usage", "on")
    assert code == 0 and UsageStore().sharing is True and "sharing" in out.lower()
    code, out, _ = run(capsys, "usage", "off")
    assert code == 0 and UsageStore().sharing is False and UsageStore().consent == "decline"
    assert "nothing is sent" in out.lower()


def test_log_is_empty_when_nothing_was_sent(capsys):
    _, out, _ = run(capsys, "usage", "log")
    assert "nothing has been sent" in out.lower()


def test_log_shows_the_events_exactly_as_sent_newest_first(capsys):
    store = UsageStore()
    store.set_consent("share")
    store.record("first_sync_completed", {"source_type": "jira", "ms_since_first_open": 4100})
    store.flush(Recorder())
    store.send_user_chosen("source_requested", {"source_id": "notion"}, Recorder())
    _, out, _ = run(capsys, "usage", "log")
    lines = [line for line in out.splitlines() if line.strip()]
    assert "source_requested" in lines[0] and "goes to the team" in lines[0]
    assert "first_sync_completed" in lines[1]
    assert json.dumps({"source_type": "jira", "ms_since_first_open": 4100}) in lines[1]


def test_log_json_is_machine_readable(capsys):
    store = UsageStore()
    store.set_consent("share")
    store.record("e", {"n": 1})
    store.flush(Recorder())
    _, out, _ = run(capsys, "usage", "log", "--json")
    (entry,) = json.loads(out)
    assert entry["event"] == "e" and entry["properties"] == {"n": 1}


def test_reset_id_gives_a_new_random_install_id(capsys):
    old = UsageStore().install_id
    code, out, _ = run(capsys, "usage", "reset-id")
    new = UsageStore().install_id
    assert code == 0 and new != old and new in out


def test_the_consent_counter_switch_is_off_by_default_and_can_be_turned_on(capsys):
    assert UsageStore().counter_enabled is False
    run(capsys, "usage", "counter", "on")
    assert UsageStore().counter_enabled is True
    run(capsys, "usage", "counter", "off")
    assert UsageStore().counter_enabled is False


def test_deciding_on_the_command_line_is_counted_when_the_counter_is_on(capsys, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().set_counter_enabled(True)
    run(capsys, "usage", "off")
    assert [e["properties"]["outcome"] for e in transport.events] == ["decline"]


# --- first interactive run: asks once --------------------------------------------------------------------------------
@pytest.mark.parametrize("answer,consent", [("s", "share"), ("share", "share"), ("d", "decline"), ("D", "decline")])
def test_the_first_interactive_run_asks_once_and_stores_the_answer(capsys, monkeypatch, answer, consent):
    interactive(monkeypatch, answer)
    code, out, err = run(capsys, *CHEAP)
    assert code == 0 and UsageStore().consent == consent
    assert "never your content" in err.lower() or "never" in err.lower()          # the card says what is never sent
    run(capsys, *CHEAP)                                                           # a second run does not ask again (the fake input would raise)


@pytest.mark.parametrize("answer", ["", "   ", "maybe", "x"])
def test_pressing_enter_or_anything_else_skips_and_nothing_is_shared(capsys, monkeypatch, answer):
    interactive(monkeypatch, answer)
    run(capsys, *CHEAP)
    assert UsageStore().consent == "unanswered" and UsageStore().sharing is False
    run(capsys, *CHEAP)                                                           # asked only once


def test_the_question_does_not_pre_select_an_answer(capsys, monkeypatch):
    interactive(monkeypatch, "")
    _, _, err = run(capsys, *CHEAP)
    assert "[s]" in err.lower() and "[d]" in err.lower()
    assert "default" not in err.lower()


def test_the_question_goes_to_stderr_so_stdout_stays_clean(capsys, monkeypatch):
    interactive(monkeypatch, "")
    _, out, err = run(capsys, *CHEAP)
    assert "usage" not in out.lower() and "share" in err.lower()


def test_the_first_answer_is_counted_when_the_counter_is_on(capsys, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().set_counter_enabled(True)
    interactive(monkeypatch, "")
    run(capsys, *CHEAP)
    assert [e["properties"]["outcome"] for e in transport.events] == ["no_answer"]


# --- non-interactive runs --------------------------------------------------------------------------------------------
def test_a_non_interactive_run_defaults_to_not_sharing_and_prints_how_to_opt_in_once(capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: False)
    code, out, err = run(capsys, *CHEAP)
    assert code == 0 and UsageStore().sharing is False
    assert "mdpipeline usage on" in err
    assert "mdpipeline usage" not in out                                           # stdout (what scripts parse) is untouched
    _, _, err2 = run(capsys, *CHEAP)
    assert "mdpipeline usage on" not in err2


def test_a_non_interactive_run_is_not_counted_as_a_decision(capsys, monkeypatch):
    transport = Recorder()
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    UsageStore().set_counter_enabled(True)
    monkeypatch.setattr(cli, "interactive", lambda: False)
    run(capsys, *CHEAP)
    assert transport.events == []                    # nobody saw a card: the rate is over people who saw it


@pytest.mark.parametrize("command", ["mcp", "usage"])
def test_the_mcp_server_and_the_usage_command_never_trigger_the_notice(command):
    assert cli.needs_usage_notice(command) is False


def test_other_commands_do_trigger_the_notice():
    assert cli.needs_usage_notice("sync") is True and cli.needs_usage_notice("github-app") is True


def test_once_answered_nothing_is_asked_and_nothing_is_printed(capsys, monkeypatch):
    UsageStore().set_consent("decline")
    interactive(monkeypatch)                          # any input() call would raise
    _, _, err = run(capsys, *CHEAP)
    assert "usage" not in err.lower()


# --- MD-156: never wait on a question nobody can see -------------------------------------------------------------------
def test_a_terminal_stdin_with_a_hidden_stderr_never_asks_and_gives_the_hint(capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr(cli, "stderr_visible", lambda: False)

    def no_input(prompt=""):
        raise AssertionError("asked a question nobody can see")

    monkeypatch.setattr(builtins, "input", no_input)
    code, out, err = run(capsys, *CHEAP)
    assert code == 0 and UsageStore().sharing is False and "mdpipeline usage on" in err


def test_a_real_command_with_a_terminal_stdin_and_a_piped_stderr_answers_within_seconds(tmp_path):
    import os
    import pty
    import subprocess
    import sys

    home = str(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MDPIPELINE", "XDG_"))}
    env.update(HOME=home, XDG_CONFIG_HOME=home, XDG_DATA_HOME=home, MDPIPELINE_DATA_DIR=home, MDPIPELINE_NO_BACKGROUND_START="1", MDPIPELINE_POSTHOG_KEY="")
    master, slave = pty.openpty()                                   # stdin is a terminal, as in the owner's shell
    try:
        done = subprocess.run([sys.executable, "-m", "mdpipeline", "github-app", "show"], env=env, stdin=slave, capture_output=True, text=True, timeout=20)
    finally:
        os.close(master)
        os.close(slave)
    assert done.returncode == 0 and "Client ID:" in done.stdout
