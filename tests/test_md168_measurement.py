"""MD-168 (MP-62): self-serve measurement. No conversation with the first users, so the funnel and the in-product answers must say why.

AC1 reads_folder_directly; AC2 desktop_session and AC3 linger_enabled on background_started; AC4 linger notice shown/hidden with a surface;
AC5 the stopped_before_first_sync question; AC6 allowlist, docs; AC7 these tests."""
import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mdpipeline import autostart, background, cli, feedback, scheduler, usage, usage_hooks
from mdpipeline.config import Pipeline
from mdpipeline.log import ActivityLog
from mdpipeline.usage import UsageStore
from mdpipeline.usage_events import EVENTS, sanitize
from test_background_headless import FakeSystemd
from uihelper import running_ui

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
PKG = Path(__file__).parent.parent / "src" / "mdpipeline"
STALL_ANSWERS = ["no_time", "sign_in_trouble", "step_unclear", "source_missing", "privacy_worry", "just_looking", "broke", "other"]


def queued(*names):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def own(e):
    return {k: v for k, v in e["properties"].items() if k not in ("app_version", "deployment", "session_id", "is_internal")}


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


@pytest.fixture
def headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    fake = FakeSystemd(linger="no")
    monkeypatch.setattr(autostart, "_run", fake)
    return fake


def synced(brain, add_pipeline, days_ago=0.01):
    add_pipeline(Pipeline.new("p", []))
    ts = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ActivityLog(brain).append({"ts": ts, "type": "run", "pipeline": "p", "trigger": "ui", "result": "ok", "counts": {"created": 3}, "errors": []})


# --- AC1 / AC6: reads_folder_directly ---------------------------------------------------------------------------------------
def test_the_agent_question_has_marketings_words_and_the_new_answer_second():
    q = feedback.QUESTIONS["no_agent_read_3d"]
    assert q["text"] == "No connected agent has read your files yet. Which fits best?" and q["comment_label"] == "Anything to add? (optional)"
    assert [(o["answer"], o["label"]) for o in q["options"]] == [
        ("no_agent_installed", "I don't use a coding agent"), ("reads_folder_directly", "My agent already reads the files from the folder"),
        ("dont_know_how", "I didn't know how to connect it"), ("tried_failed", "I tried and it didn't work"), ("no_time", "I haven't had time yet"),
        ("files_not_useful", "The files weren't what I needed"), ("other", "Something else")]


def test_the_answer_is_accepted_on_the_agent_question(brain, sharing):
    class T:
        def __init__(self): self.events = []
        def __call__(self, events): self.events += events; return True
    t = T()
    assert feedback.answer("no_agent_read_3d", "reads_folder_directly", "", t) == {"ok": True}
    assert t.events[0]["properties"]["answer"] == "reads_folder_directly"


@pytest.mark.parametrize("question", ["first_sync_expectation", "last_source_removed", "stopped_before_first_sync"])
def test_the_answer_is_refused_on_every_other_question(question):
    with pytest.raises(ValueError):
        feedback.answer(question, "reads_folder_directly", "", lambda events: True)


@pytest.mark.parametrize("value", ["reads_folder_directly", *STALL_ANSWERS])
def test_every_new_answer_passes_the_allowlist(value):
    assert sanitize("feedback_answered", {"question_id": "no_agent_read_3d", "answer": value}, explicit=True) == \
        {"question_id": "no_agent_read_3d", "answer": value}


def test_the_new_question_id_passes_and_an_invented_answer_does_not():
    assert sanitize("feedback_answered", {"question_id": "stopped_before_first_sync", "answer": "no_time"}, explicit=True)["question_id"] == "stopped_before_first_sync"
    assert "answer" not in sanitize("feedback_answered", {"question_id": "stopped_before_first_sync", "answer": "bored"}, explicit=True)


# --- AC5: stopped_before_first_sync ---------------------------------------------------------------------------------------
def test_the_question_has_the_fixed_values_in_order_and_goes_first():
    q = feedback.QUESTIONS["stopped_before_first_sync"]
    assert feedback.ORDER[0] == "stopped_before_first_sync"
    assert q["text"] == "Last time, setup ended before any files were synced. What got in the way?" and q["comment_label"] == "Anything to add? (optional)"
    assert [o["answer"] for o in q["options"]] == STALL_ANSWERS
    assert [o["label"] for o in q["options"]] == [
        "I ran out of time", "I couldn't sign in or get a token", "A step wasn't clear", "The source I need isn't supported yet",
        "I was worried about privacy", "I was only looking around", "Something broke", "Something else"]


def test_just_looking_is_accepted_on_the_stall_question_only():
    assert feedback.answer("stopped_before_first_sync", "just_looking", "", lambda events: True) == {"ok": True}
    for question in ("no_agent_read_3d", "first_sync_expectation", "last_source_removed"):
        with pytest.raises(ValueError):
            feedback.answer(question, "just_looking", "", lambda events: True)
    assert sanitize("feedback_answered", {"question_id": "stopped_before_first_sync", "answer": "just_looking"}, explicit=True)["answer"] == "just_looking"


def test_it_is_not_asked_in_the_session_that_ended_the_first_run(brain):
    feedback.note_first_run_ended()
    assert feedback.next_prompt(brain, NOW) is None


def test_it_is_asked_on_the_next_app_open_even_with_no_pipeline_and_sharing_off(brain):
    feedback.note_first_run_ended()
    usage.new_session()
    p = feedback.next_prompt(brain, NOW)
    assert p["question_id"] == "stopped_before_first_sync" and p["goes_to_the_team"] is True


def test_it_is_asked_once_only(brain):
    feedback.note_first_run_ended()
    usage.new_session()
    assert feedback.next_prompt(brain, NOW) is not None
    assert feedback.next_prompt(brain, NOW) is None
    usage.new_session()
    assert feedback.next_prompt(brain, NOW) is None


def test_dismissing_it_ends_it(brain):
    feedback.note_first_run_ended()
    usage.new_session()
    feedback.dismiss("stopped_before_first_sync")
    assert feedback.next_prompt(brain, NOW) is None
    assert feedback.handled()["stopped_before_first_sync"] == "dismissed"


def test_it_is_not_asked_when_a_first_sync_exists(brain, add_pipeline):
    feedback.note_first_run_ended()
    usage.new_session()
    synced(brain, add_pipeline)
    assert (feedback.next_prompt(brain, NOW) or {}).get("question_id") != "stopped_before_first_sync"


def test_it_is_not_asked_when_the_first_run_never_ended_early(brain):
    usage.new_session()
    assert feedback.next_prompt(brain, NOW) is None


def test_it_goes_before_the_other_questions(brain, add_pipeline):
    synced(brain, add_pipeline, days_ago=4)                 # also due: nothing else could be, with a sync present, but order is what counts
    assert feedback.ORDER.index("stopped_before_first_sync") < feedback.ORDER.index("first_sync_expectation")


def test_the_page_tells_the_app_the_first_run_ended_even_without_consent(brain):
    with running_ui(brain) as ui:
        status, res = ui.post("/api/feedback/first-run-ended", {})
    assert status == 200 and res == {"ok": True}
    usage.new_session()
    assert feedback.next_prompt(brain, NOW)["question_id"] == "stopped_before_first_sync"
    assert queued() == []


def test_the_first_run_page_reports_it_whether_or_not_sharing_is_on():
    js = (PKG / "ui_static" / "firstrun.js").read_text(encoding="utf-8")
    assert "/api/feedback/first-run-ended" in js


# --- AC2 / AC3: background_started ----------------------------------------------------------------------------------------
def run_background(how="manual", monkeypatch=None):
    monkeypatch.setattr(scheduler, "tick_all", lambda now=None: [])
    background.run(max_ticks=1, sleep=lambda s: None, how=how)


@pytest.mark.parametrize("display,expected", [(None, "no"), (":0", "yes")])
def test_desktop_session_follows_the_display(sharing, monkeypatch, display, expected):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if display:
        monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="no"))
    run_background(monkeypatch=monkeypatch)
    assert own(queued("background_started")[0])["desktop_session"] == expected


@pytest.mark.parametrize("linger,expected", [("no", "no"), ("yes", "yes")])
def test_linger_enabled_is_read_from_the_system(sharing, headless, monkeypatch, linger, expected):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger=linger))
    run_background(monkeypatch=monkeypatch)
    assert own(queued("background_started")[0])["linger_enabled"] == expected


def test_without_systemd_linger_is_unknown(sharing, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeSystemd(available=False))
    run_background(monkeypatch=monkeypatch)
    assert own(queued("background_started")[0])["linger_enabled"] == "unknown"


def test_off_linux_both_are_unknown(sharing, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    run_background(monkeypatch=monkeypatch)
    assert {k: own(queued("background_started")[0])[k] for k in ("desktop_session", "linger_enabled")} == {"desktop_session": "unknown", "linger_enabled": "unknown"}


@pytest.mark.parametrize("display", [None, ":0"])
@pytest.mark.parametrize("linger", ["no", "yes"])
def test_desktop_session_and_the_linger_line_cannot_disagree(monkeypatch, display, linger):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if display:
        monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger=linger))
    shown = autostart.desktop_state() == "no" and autostart.linger_state() == "no"
    assert shown is autostart.linger_notice()


def test_the_app_only_reads_linger_never_changes_it(sharing, headless, monkeypatch):
    run_background(monkeypatch=monkeypatch)
    assert all("enable-linger" not in call for call in headless.calls) and all(call[0] != "sudo" for call in headless.calls)
    for path in PKG.glob("*.py"):                      # nothing in the app runs it: the only mentions are in text shown to the person
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and any(isinstance(a, ast.Constant) and a.value == "enable-linger" for a in node.args):
                pytest.fail(f"{path.name} runs enable-linger")


def test_the_new_values_are_in_the_allowlist():
    for prop in ("desktop_session", "linger_enabled"):
        for value in ("yes", "no", "unknown"):
            assert sanitize("background_started", {"how": "manual", prop: value})[prop] == value
        assert prop not in sanitize("background_started", {"how": "manual", prop: "maybe"})


# --- AC4: the linger notice events ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("surface", ["ready", "home", "settings", "cli"])
def test_notice_shown_accepts_kind_linger_with_a_surface(surface):
    assert sanitize("notice_shown", {"kind": "linger", "surface": surface}) == {"kind": "linger", "surface": surface}
    assert sanitize("notice_hidden", {"kind": "linger", "surface": surface}) == {"kind": "linger", "surface": surface}


def test_an_unknown_surface_or_kind_is_dropped():
    assert sanitize("notice_shown", {"kind": "linger", "surface": "/home/user"}) == {"kind": "linger"}
    assert sanitize("notice_hidden", {"kind": "stale", "surface": "home"}) == {"surface": "home"}


def test_the_existing_reliability_notice_is_unchanged():
    assert sanitize("notice_shown", {"kind": "sync_failed"}) == {"kind": "sync_failed"}


def test_shown_fires_once_per_surface_per_day(sharing):
    day1, day2 = datetime(2026, 10, 20, 8, tzinfo=timezone.utc), datetime(2026, 10, 21, 8, tzinfo=timezone.utc)
    assert usage_hooks.linger_notice_shown("home", now=day1) is True
    assert usage_hooks.linger_notice_shown("home", now=day1) is False
    assert usage_hooks.linger_notice_shown("settings", now=day1) is True
    assert usage_hooks.linger_notice_shown("home", now=day2) is True
    assert [own(e) for e in queued("notice_shown")] == [{"kind": "linger", "surface": "home"}, {"kind": "linger", "surface": "settings"},
                                                        {"kind": "linger", "surface": "home"}]


def test_the_page_reports_the_notice_through_the_same_once_a_day_rule(brain, sharing):
    with running_ui(brain) as ui:
        for _ in range(3):
            ui.post("/api/usage/event", {"event": "notice_shown", "properties": {"kind": "linger", "surface": "ready"}})
    assert [own(e) for e in queued("notice_shown")] == [{"kind": "linger", "surface": "ready"}]


def test_the_cli_reports_it_once_a_day_for_schedule_background_on_and_show(sharing, headless, brain, capsys):
    cli.main(["--brain", str(brain.root), "schedule", "background", "on"])
    cli.main(["--brain", str(brain.root), "schedule"])
    assert [own(e) for e in queued("notice_shown")] == [{"kind": "linger", "surface": "cli"}]


def test_the_cli_reports_nothing_when_the_line_is_not_shown(sharing, brain, monkeypatch, capsys):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(autostart, "_run", FakeSystemd(linger="no"))
    cli.main(["--brain", str(brain.root), "schedule", "background", "on"])
    assert queued("notice_shown") == []


def test_hide_on_home_sends_notice_hidden(brain, sharing, headless):
    with running_ui(brain) as ui:
        assert ui.post("/api/background/linger-hide", {})[0] == 200
    assert [own(e) for e in queued("notice_hidden")] == [{"kind": "linger", "surface": "home"}]


def test_the_three_screens_report_the_notice():
    app = (PKG / "ui_static" / "app.js").read_text(encoding="utf-8")
    first = (PKG / "ui_static" / "firstrun.js").read_text(encoding="utf-8")
    assert 'noteLinger("home")' in app and 'noteLinger("settings")' in app and 'noteLinger("ready")' in first


# --- AC6: docs ------------------------------------------------------------------------------------------------------------
def test_what_we_send_describes_each_change_in_plain_words():
    page = (Path(__file__).parent.parent / "docs" / "what-we-send.md").read_text(encoding="utf-8").lower()
    for phrase in ("`notice_hidden`", "desktop session", "linger", "what got in the way", "reads the files from the folder"):
        assert phrase in page, phrase


def test_no_new_property_carries_free_text():
    from mdpipeline.usage_events import UserText
    for name in ("background_started", "notice_shown", "notice_hidden"):
        assert not any(isinstance(rule, UserText) for rule in EVENTS[name].props.values())


# --- AC2 amendment (2026-10-20): a systemd-started process has no DISPLAY even on a desktop, so ask logind too ------------
class FakeLogind:
    """`loginctl list-sessions` / `show-session` / `show-user` for a box with the given sessions: (id, user, type)."""

    def __init__(self, sessions=(), linger="no", available=True):
        self.sessions, self.linger, self.available, self.calls = list(sessions), linger, available, []

    def __call__(self, *args):
        from types import SimpleNamespace
        self.calls.append(args)
        if not self.available:
            return None
        if args[:2] == ("loginctl", "list-sessions"):
            return SimpleNamespace(returncode=0, stdout="".join(f"{i} 1000 {u} seat0 -\n" for i, u, _ in self.sessions))
        if args[:2] == ("loginctl", "show-session"):
            sid = args[2]
            for i, u, t in self.sessions:
                if i == sid:
                    return SimpleNamespace(returncode=0, stdout=f"Type={t}\nName={u}\n")
            return SimpleNamespace(returncode=1, stdout="")
        if args[:2] == ("loginctl", "show-user"):
            return SimpleNamespace(returncode=0, stdout=f"Linger={self.linger}\n")
        return SimpleNamespace(returncode=0, stdout="")


@pytest.fixture
def no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("USER", "avi")


@pytest.mark.parametrize("kind", ["x11", "wayland", "mir"])
def test_a_service_without_display_still_sees_the_users_graphical_session(no_display, monkeypatch, kind):
    monkeypatch.setattr(autostart, "_run", FakeLogind([("2", "avi", kind)]))
    assert autostart.desktop_state() == "yes" and autostart.linger_notice() is False


def test_an_ssh_session_only_is_no(no_display, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeLogind([("5", "avi", "tty")]))
    assert autostart.desktop_state() == "no" and autostart.linger_notice() is True


def test_another_users_graphical_session_does_not_count(no_display, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeLogind([("3", "bob", "x11"), ("5", "avi", "tty")]))
    assert autostart.desktop_state() == "no"


def test_no_display_and_no_loginctl_is_unknown_and_the_line_stays_hidden(no_display, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeLogind(available=False))
    assert autostart.desktop_state() == "unknown" and autostart.linger_notice() is False


def test_a_display_in_the_process_is_yes_without_asking_logind(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    fake = FakeLogind([])
    monkeypatch.setattr(autostart, "_run", fake)
    assert autostart.desktop_state() == "yes" and fake.calls == []


def test_the_event_for_a_user_service_on_a_desktop_says_yes(sharing, no_display, monkeypatch):
    monkeypatch.setattr(autostart, "_run", FakeLogind([("2", "avi", "wayland")], linger="no"))
    run_background(how="user_service", monkeypatch=monkeypatch)
    assert own(queued("background_started")[0]) == {"how": "user_service", "desktop_session": "yes", "linger_enabled": "no"}


def test_asking_logind_is_read_only(no_display, monkeypatch):
    fake = FakeLogind([("2", "avi", "x11")])
    monkeypatch.setattr(autostart, "_run", fake)
    autostart.desktop_state()
    assert {c[1] for c in fake.calls} <= {"list-sessions", "show-session"}
