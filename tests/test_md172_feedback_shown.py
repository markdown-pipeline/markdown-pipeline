"""MD-172: `feedback_shown {question_id}` is sent when an in-product question card is first shown in a UI session, so the answer rate
(`feedback_answered` / `feedback_shown`) can be read. Same consent and `is_internal` rules as every other usage event. Content-free."""
import json
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from mdpipeline import autostart, notices, uiapi, usage  # noqa: E402
from mdpipeline.config import Pipeline  # noqa: E402
from mdpipeline.log import ActivityLog  # noqa: E402
from mdpipeline.usage import UsageStore  # noqa: E402
from mdpipeline.usage_events import EVENTS, QUESTION_ID, sanitize  # noqa: E402
from test_ui_browser import browser, open_activity, open_app, page  # noqa: E402,F401
from uihelper import running_ui  # noqa: E402

GLOBALS = ("app_version", "deployment", "session_id", "is_internal")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(autostart, "enable", lambda **kw: None)
    monkeypatch.setattr(autostart, "disable", lambda **kw: True)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued(*names):
    entries = json.loads((usage.data_dir() / "usage.json").read_text()).get("queue", [])
    return [e for e in entries if not names or e["event"] in names]


def own(e):
    return {k: v for k, v in e["properties"].items() if k not in GLOBALS}


def wait_for(name, count=1, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if len(queued(name)) >= count:
            return queued(name)
        time.sleep(0.05)
    raise AssertionError(f"{name} x{count} was not reported; queue: {[e['event'] for e in queued()]}")


def synced(brain, add_pipeline, make_source):
    add_pipeline(Pipeline.new("acme", [str(make_source("a", {"1.md": "one"}))]))
    ActivityLog(brain).append({"type": "run", "pipeline": "acme", "trigger": "ui", "result": "ok", "counts": {"created": 1}, "errors": []})


# --- the event in the catalog (AC1, AC2) ----------------------------------------------------------------------------------------------
def test_the_event_is_in_the_catalog_owned_by_the_ui_with_the_question_id_and_nothing_else():
    event = EVENTS["feedback_shown"]
    assert event.surfaces == ("ui",) and event.explicit is False
    assert list(event.props) == ["question_id"] and event.props["question_id"] is QUESTION_ID


@pytest.mark.parametrize("question_id", QUESTION_ID.values)
def test_every_question_id_is_accepted(question_id):
    assert sanitize("feedback_shown", {"question_id": question_id}) == {"question_id": question_id}


def test_anything_but_the_question_id_is_dropped_so_the_event_stays_content_free():
    clean = sanitize("feedback_shown", {"question_id": "no_agent_read_3d", "answer": "yes", "comment": "private", "text": "Did you get what you expected?"})
    assert clean == {"question_id": "no_agent_read_3d"}


def test_a_question_id_that_is_not_in_the_enum_is_dropped():
    assert "question_id" not in (sanitize("feedback_shown", {"question_id": "my own question"}) or {})


def test_the_event_is_documented_in_the_generated_event_list():
    from mdpipeline import usage_docs

    assert "feedback_shown" in usage_docs.render_doc()


# --- consent and is_internal (AC2) ----------------------------------------------------------------------------------------------------
def test_with_sharing_on_the_page_report_is_queued_with_the_same_globals_as_every_event(sharing):
    assert uiapi.usage_event({"event": "feedback_shown", "properties": {"question_id": "first_sync_expectation"}}) == {"queued": True}
    (e,) = queued("feedback_shown")
    assert own(e) == {"question_id": "first_sync_expectation"}
    assert set(GLOBALS) <= set(e["properties"]) and e["properties"]["is_internal"] is True


def test_with_sharing_off_nothing_is_queued():
    UsageStore().set_consent("decline")
    assert uiapi.usage_event({"event": "feedback_shown", "properties": {"question_id": "first_sync_expectation"}}) == {"queued": False}
    assert queued("feedback_shown") == []


def test_with_no_choice_made_yet_nothing_is_queued():
    assert uiapi.usage_event({"event": "feedback_shown", "properties": {"question_id": "first_sync_expectation"}}) == {"queued": False}
    assert queued("feedback_shown") == []


# --- the card in a real browser (AC1, AC3) --------------------------------------------------------------------------------------------
def test_the_card_being_shown_reports_it_once_with_only_the_question_id(page, brain, add_pipeline, make_source, sharing):
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#fb-prompt")).to_be_visible()
        (e,) = wait_for("feedback_shown")
    assert own(e) == {"question_id": "first_sync_expectation"}


def test_redrawing_the_card_does_not_report_it_again(page, brain, add_pipeline, make_source, sharing):
    """Choosing an answer, typing and leaving Home and coming back all redraw the card."""
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        card = page.locator("#fb-prompt")
        expect(card).to_be_visible()
        wait_for("feedback_shown")
        card.get_by_role("button", name="Partly").click()
        card.get_by_label("Anything to add? (optional)").fill("x")
        open_activity(page)
        page.get_by_role("navigation", name="Screens").get_by_role("button", name="Pipelines").click()
        expect(page.locator("#fb-prompt")).to_be_visible()
        time.sleep(0.5)
        assert len(queued("feedback_shown")) == 1


def test_a_reload_after_the_card_was_shown_does_not_report_a_second_time(page, brain, add_pipeline, make_source, sharing):
    """The server never shows a question twice, so there is no card after a reload and nothing to report."""
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        wait_for("feedback_shown")
        page.reload()
        expect(page.get_by_role("heading", name="Pipelines")).to_be_visible()
        expect(page.locator("#fb-prompt")).to_have_count(0)
        time.sleep(0.5)
    assert len(queued("feedback_shown")) == 1


def test_nothing_is_reported_when_no_question_is_due(page, brain, sharing):
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#fb-prompt")).to_have_count(0)
        time.sleep(0.6)
    assert queued("feedback_shown") == []


def test_nothing_is_reported_when_the_person_did_not_choose_to_share(page, brain, add_pipeline, make_source):
    synced(brain, add_pipeline, make_source)
    UsageStore().set_consent("decline")
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#fb-prompt")).to_be_visible()
        time.sleep(0.6)
    assert queued("feedback_shown") == []


def test_dismissing_the_card_does_not_report_a_second_time(page, brain, add_pipeline, make_source, sharing):
    synced(brain, add_pipeline, make_source)
    with running_ui(brain) as ui:
        open_app(page, ui)
        wait_for("feedback_shown")
        page.locator("#fb-prompt").get_by_role("button", name="Dismiss").click()
        expect(page.locator("#fb-prompt")).to_have_count(0)
        time.sleep(0.5)
    assert len(queued("feedback_shown")) == 1


def test_the_question_shown_on_the_start_screen_after_the_last_source_was_removed_reports_its_own_id(page, brain, sharing):
    ActivityLog(brain).append({"type": "run", "pipeline": "acme", "trigger": "ui", "result": "ok", "counts": {"created": 2}, "errors": []})
    ActivityLog(brain).event("remove", "acme", "ui", unowned=2, deleted=0, purge=False, paths=[])
    with running_ui(brain) as ui:
        open_app(page, ui)
        expect(page.locator("#fb-prompt")).to_contain_text("Why did you remove it?")
        (e,) = wait_for("feedback_shown")
    assert own(e) == {"question_id": "last_source_removed"}


def test_the_public_row_says_each_card_is_sent_once_per_card_on_a_computer():
    """Marketing MM-130 / product DS-P-1022-1: the server never shows a question twice, so the event is sent once per card on a computer,
    not once per page load. The privacy row must say that (and never promise a repeat per page)."""
    from pathlib import Path

    row = next(r for r in (Path(__file__).parent.parent / "docs/what-we-send.md").read_text(encoding="utf-8").splitlines() if "`feedback_shown`" in r)
    assert "each time a page shows that card" not in row
    assert "Each card appears only once on a computer, so this is sent once per card." in row
    assert "Never the question's text or your answer" in row
