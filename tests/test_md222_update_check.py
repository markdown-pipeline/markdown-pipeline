"""MD-222: the app asks PyPI at most once a day whether a newer version exists, and tells the person once a day, with the one command for their install
method. It never delays or breaks a command, is silent offline, can be turned off, and prints nothing on the MCP server's stdout."""
import io
import json
import urllib.error

import pytest

from mdpipeline import cli, updatecheck, usage
from mdpipeline.usage import UsageStore

DAY = 86400.0
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def check_on(monkeypatch):
    """conftest turns the check off for every other test; these tests are about it."""
    monkeypatch.delenv(updatecheck.ENV_OFF, raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(updatecheck, "installed_version", lambda: "0.1.17")
    monkeypatch.setattr(updatecheck, "method", lambda: "uv")


def latest(version):
    calls = []

    def fetch():
        calls.append(1)
        return version

    fetch.calls = calls
    return fetch


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- AC1: the request ------------------------------------------------------------------------------------------------------------------------
def test_the_request_goes_to_pypi_with_a_two_second_timeout_and_sends_nothing_else():
    seen = {}

    def opener(request, timeout=None):
        seen["url"], seen["timeout"], seen["headers"], seen["data"] = request.full_url, timeout, dict(request.header_items()), request.data
        return FakeResponse(json.dumps({"info": {"version": "0.1.19"}}).encode())

    assert updatecheck.fetch_latest(opener) == "0.1.19"
    assert seen["url"] == "https://pypi.org/pypi/mdpipeline/json"
    assert seen["timeout"] <= 2 and seen["data"] is None
    assert set(k.lower() for k in seen["headers"]) <= {"accept", "user-agent"}


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError(), OSError("x"), ValueError("x")])
def test_offline_or_slow_is_silent_and_gives_no_version(failure):
    def opener(request, timeout=None):
        raise failure

    assert updatecheck.fetch_latest(opener) is None


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"info": {"version": "1.0.0rc1"}}', b'{"info": {"version": 3}}'])
def test_an_answer_that_is_not_a_plain_release_number_is_ignored(body):
    assert updatecheck.fetch_latest(lambda request, timeout=None: FakeResponse(body)) is None


def test_it_asks_at_most_once_per_24_hours_and_keeps_the_answer():
    fetch = latest("0.1.19")
    updatecheck.refresh(T0, fetch)
    updatecheck.refresh(T0 + DAY - 1, fetch)
    assert len(fetch.calls) == 1
    updatecheck.refresh(T0 + DAY + 1, fetch)
    assert len(fetch.calls) == 2


def test_a_failed_check_also_waits_a_day_and_keeps_the_last_good_answer():
    updatecheck.refresh(T0, latest("0.1.19"))
    offline = latest(None)
    updatecheck.refresh(T0 + DAY + 1, offline)
    updatecheck.refresh(T0 + DAY + 100, offline)
    assert len(offline.calls) == 1
    assert updatecheck.available(T0 + DAY + 200) == {"from": "0.1.17", "to": "0.1.19"}


# --- AC2: newer, equal, older ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("on_pypi, expected", [("0.1.19", True), ("0.1.18", True), ("0.2.0", True), ("0.10.0", True), ("0.1.17", False), ("0.1.16", False), ("0.0.99", False)])
def test_only_a_newer_version_counts(on_pypi, expected):
    updatecheck.refresh(T0, latest(on_pypi))
    assert (updatecheck.available(T0) is not None) is expected


def test_nothing_is_available_before_any_check():
    assert updatecheck.available(T0) is None


# --- AC2: the command that fits the install method ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method, command", [("uv", "mdpipeline update"), ("script", "mdpipeline update"), ("pipx", "mdpipeline update"), ("pip", "mdpipeline update"),
                                              ("uvx", "uvx mdpipeline update"), ("registry", "uvx mdpipeline update")])
def test_the_line_names_both_versions_and_the_one_command_for_the_install_method(monkeypatch, method, command):
    monkeypatch.setattr(updatecheck, "method", lambda: method)
    updatecheck.refresh(T0, latest("0.1.19"))
    line = updatecheck.cli_line(T0)
    assert line == f"Markdown Pipeline 0.1.19 is out (you have 0.1.17). Update: {command}"


def test_a_source_checkout_keeps_the_git_pull_text(monkeypatch):
    monkeypatch.setattr(updatecheck, "method", lambda: "source")
    updatecheck.refresh(T0, latest("0.1.19"))
    assert "git pull" in updatecheck.cli_line(T0)


# --- AC3: once a day per surface, dismissible on the page ---------------------------------------------------------------------------------------
def test_the_cli_line_shows_once_a_day():
    updatecheck.refresh(T0, latest("0.1.19"))
    assert updatecheck.cli_line(T0)
    assert updatecheck.cli_line(T0 + 60) is None
    assert updatecheck.cli_line(T0 + DAY + 60)


def test_the_two_surfaces_are_counted_separately():
    updatecheck.refresh(T0, latest("0.1.19"))
    assert updatecheck.cli_line(T0)
    assert updatecheck.app_notice(T0)["to"] == "0.1.19"


def test_the_page_keeps_showing_the_notice_until_it_is_dismissed_and_a_dismissal_lasts_until_the_next_version():
    updatecheck.refresh(T0, latest("0.1.19"))
    assert updatecheck.app_notice(T0) and updatecheck.app_notice(T0 + 3600)
    updatecheck.dismiss()
    assert updatecheck.app_notice(T0 + 7200) is None
    assert updatecheck.app_notice(T0 + 3 * DAY) is None
    updatecheck.refresh(T0 + 4 * DAY, latest("0.1.20"))
    assert updatecheck.app_notice(T0 + 4 * DAY)["to"] == "0.1.20"


def test_the_app_notice_carries_the_words_and_the_command():
    updatecheck.refresh(T0, latest("0.1.19"))
    notice = updatecheck.app_notice(T0)
    assert notice["command"] == "mdpipeline update" and notice["text"] == "Version 0.1.19 is out. You have 0.1.17."
    assert notice["whats_new"] == "https://github.com/markdown-pipeline/markdown-pipeline/releases/tag/v0.1.19"


@pytest.mark.parametrize("word", ["!", "outdated", "critical", "should", "please", "security", "telemetry", "phone"])
def test_the_words_are_plain_facts_with_no_urgency(word):
    updatecheck.refresh(T0, latest("0.1.19"))
    notice = updatecheck.app_notice(T0)
    assert word not in (updatecheck.cli_line(T0) + notice["text"]).lower()


# --- AC4: off switch ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_the_environment_variable_turns_everything_off(monkeypatch, value):
    monkeypatch.setenv(updatecheck.ENV_OFF, value)
    fetch = latest("0.1.19")
    updatecheck.refresh(T0, fetch)
    assert fetch.calls == [] and updatecheck.cli_line(T0) is None and updatecheck.app_notice(T0) is None


def test_turning_it_off_hides_an_answer_that_was_already_kept(monkeypatch):
    updatecheck.refresh(T0, latest("0.1.19"))
    monkeypatch.setenv(updatecheck.ENV_OFF, "1")
    assert updatecheck.available(T0) is None


@pytest.mark.parametrize("value", ["", "0", "no"])
def test_other_values_leave_it_on(monkeypatch, value):
    monkeypatch.setenv(updatecheck.ENV_OFF, value)
    assert updatecheck.enabled()


def test_the_setting_turns_it_off_and_back_on_and_survives_restarts():
    updatecheck.set_enabled(False)
    assert not updatecheck.enabled()
    fetch = latest("0.1.19")
    updatecheck.refresh(T0, fetch)
    assert fetch.calls == [] and updatecheck.cli_line(T0) is None
    updatecheck.set_enabled(True)
    assert updatecheck.enabled()


def test_the_update_check_command_sets_and_shows_the_setting(capsys):
    assert cli.main(["update-check", "off"]) == 0
    assert not updatecheck.enabled()
    capsys.readouterr()
    assert cli.main(["update-check"]) == 0
    assert "off" in capsys.readouterr().out
    assert cli.main(["update-check", "on"]) == 0 and updatecheck.enabled()


def test_usage_off_does_not_stop_the_version_check():
    UsageStore().set_consent("decline")
    fetch = latest("0.1.19")
    updatecheck.refresh(T0, fetch)
    assert len(fetch.calls) == 1 and updatecheck.cli_line(T0)


# --- AC10: CI -----------------------------------------------------------------------------------------------------------------------------------
def test_with_CI_set_the_command_line_stays_silent_and_does_not_ask(monkeypatch, capsys):
    updatecheck.refresh(T0, latest("0.1.19"))
    monkeypatch.setenv("CI", "true")
    asked = []
    monkeypatch.setattr(updatecheck, "refresh", lambda *a, **k: asked.append(1))
    updatecheck.after_command("status")
    out, err = capsys.readouterr()
    assert out == "" and err == "" and asked == []


# --- AC6: usage events (with consent only) -------------------------------------------------------------------------------------------------------
@pytest.fixture
def sharing(monkeypatch):
    UsageStore().set_consent("share")
    sent = []
    real = usage.emit

    def spy(name, /, **props):
        sent.append((name, props))
        return real(name, **props)

    monkeypatch.setattr(usage, "emit", spy)
    return sent


def test_showing_the_cli_line_reports_the_versions_and_the_surface(sharing):
    updatecheck.refresh(T0, latest("0.1.19"))
    updatecheck.cli_line(T0)
    assert ("update_notice_shown", {"from_version": "0.1.17", "to_version": "0.1.19", "surface": "cli"}) in sharing


def test_the_page_notice_reports_once_a_day_whatever_the_redraws(sharing):
    updatecheck.refresh(T0, latest("0.1.19"))
    for step in range(5):
        updatecheck.app_notice(T0 + step)
    assert [s for s in sharing if s[0] == "update_notice_shown"] == [("update_notice_shown", {"from_version": "0.1.17", "to_version": "0.1.19", "surface": "app"})]


def test_the_next_run_on_the_new_version_reports_acted_once(monkeypatch, sharing):
    updatecheck.refresh(T0, latest("0.1.19"))
    updatecheck.cli_line(T0)
    updatecheck.note_acted()
    assert not [s for s in sharing if s[0] == "update_notice_acted"]       # still on 0.1.17
    monkeypatch.setattr(updatecheck, "installed_version", lambda: "0.1.19")
    updatecheck.note_acted()
    updatecheck.note_acted()
    assert [s for s in sharing if s[0] == "update_notice_acted"] == [("update_notice_acted", {"from_version": "0.1.17", "to_version": "0.1.19"})]


def test_the_events_are_in_the_allowlist_and_nothing_else_passes():
    from mdpipeline.usage_events import EVENTS, sanitize

    assert set(EVENTS["update_notice_shown"].props) == {"from_version", "to_version", "surface"}
    assert set(EVENTS["update_notice_acted"].props) == {"from_version", "to_version"}
    assert sanitize("update_notice_shown", {"from_version": "0.1.17", "to_version": "0.1.19", "surface": "/home/me"}) == {"from_version": "0.1.17", "to_version": "0.1.19"}
    assert sanitize("update_notice_shown", {"from_version": "0.1.17", "to_version": "0.1.19", "surface": "cli", "path": "/x"}) == \
        {"from_version": "0.1.17", "to_version": "0.1.19", "surface": "cli"}


def test_no_event_without_consent():
    updatecheck.refresh(T0, latest("0.1.19"))
    assert updatecheck.cli_line(T0)             # the person still sees the line; only the report needs consent
    assert not UsageStore().sharing


# --- the command line ----------------------------------------------------------------------------------------------------------------------------
def test_a_command_prints_the_line_after_its_output_on_stderr(capsys, monkeypatch):
    updatecheck.refresh(T0, latest("0.1.19"))
    monkeypatch.setattr(updatecheck, "refresh", lambda *a, **k: None)
    updatecheck.after_command("status")
    out, err = capsys.readouterr()
    assert out == "" and "0.1.19" in err


@pytest.mark.parametrize("command", ["mcp", "ui", "start", "update", "background", "install-note", "usage"])
def test_these_commands_never_print_it_and_mcp_does_not_even_ask(capsys, monkeypatch, command):
    updatecheck.refresh(T0, latest("0.1.19"))
    asked = []
    monkeypatch.setattr(updatecheck, "refresh", lambda *a, **k: asked.append(1))
    updatecheck.after_command(command)
    out, err = capsys.readouterr()
    assert out == "" and err == "" and (command != "mcp" or asked == [])


def test_after_command_never_raises_and_never_blocks_the_command(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(updatecheck, "refresh", boom)
    updatecheck.after_command("status")


def test_main_runs_the_check_after_the_command(monkeypatch):
    seen = []
    monkeypatch.setattr(updatecheck, "after_command", lambda command: seen.append(command))
    monkeypatch.setattr(cli, "_main", lambda args: 0)
    assert cli.main(["status"]) == 0 and seen == ["status"]


def test_the_mcp_server_does_not_touch_stdout(capsys, monkeypatch):
    updatecheck.refresh(T0, latest("0.1.19"))
    monkeypatch.setattr(cli, "_main", lambda args: 0)
    cli.main(["mcp"])
    assert capsys.readouterr().out == ""


# --- the app page --------------------------------------------------------------------------------------------------------------------------------
def test_the_page_state_carries_the_notice_and_hide_removes_it(brain_dir, monkeypatch):
    from mdpipeline import uiapi
    from mdpipeline.brain import init_brain

    brain = init_brain(brain_dir)
    monkeypatch.setattr(updatecheck, "refresh_in_background", lambda: None)
    updatecheck.refresh(T0, latest("0.1.19"))
    assert uiapi.state_view(brain)["update_notice"]["to"] == "0.1.19"
    updatecheck.dismiss()
    assert uiapi.state_view(brain)["update_notice"] is None


def test_the_page_state_has_no_notice_when_the_check_is_off(brain_dir, monkeypatch):
    from mdpipeline import uiapi
    from mdpipeline.brain import init_brain

    brain = init_brain(brain_dir)
    updatecheck.refresh(T0, latest("0.1.19"))
    monkeypatch.setenv(updatecheck.ENV_OFF, "1")
    assert uiapi.state_view(brain)["update_notice"] is None


# --- every fixed install method gets a command that works for it (marketing, MM-172) -----------------------------------------------------------
def test_every_install_method_maps_to_a_command_that_the_update_command_can_run(monkeypatch):
    from mdpipeline import lifecycle
    from mdpipeline.usage_events import INSTALL_METHOD

    for kind in INSTALL_METHOD.values:
        monkeypatch.setattr(updatecheck, "method", lambda kind=kind: kind)
        shown = updatecheck.command()
        if kind == "source":
            assert shown == updatecheck.SOURCE_COMMAND and lifecycle.update_command(kind) is None
        else:
            assert shown in ("mdpipeline update", "uvx mdpipeline update"), kind
            assert lifecycle.update_command(kind), kind        # `mdpipeline update` has something to run for it
            assert shown.startswith("uvx ") == (kind in ("uvx", "registry"))


def test_the_docs_name_the_headers_the_code_sends():
    from pathlib import Path

    page = " ".join((Path(__file__).parent.parent / "docs" / "what-we-send.md").read_text(encoding="utf-8").split())
    assert "only two standard headers: `Accept: application/json` and Python's default `User-Agent`" in page


@pytest.mark.parametrize("url", ["http://pypi.org/pypi/mdpipeline/json", "https://evil.example/pypi/mdpipeline/json", "https://pypi.org/pypi/mdpipeline/json?id=1"])
def test_only_pypi_over_https_without_a_query_string_is_allowed(url):
    with pytest.raises(ValueError):
        updatecheck._check_url(url)
