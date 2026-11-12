"""MD-232 AC5, AC6, D4, D5: marketing's approved wording (v3, MM-175, LR-1111-6) on every surface that tells the person about the count.

The strings are pinned here verbatim on purpose: a change to the wording is a founder decision, not a refactor."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdpipeline import cli, usage
from mdpipeline.cli import main
from mdpipeline.usage import UsageStore

ROOT = Path(__file__).parent.parent
CARD_LINE = "Either answer sends one anonymous count of which button you pressed, once, with no install ID. If you skip this, nothing is sent."
IP_SENTENCE = ("Like any web request, each one reaches PostHog with your IP address. Our PostHog project discards it, "
               "and no location is worked out from it.")
MD233_LINE = ("Versions before 0.1.24 didn't ask for this, so PostHog worked out an approximate location (country and city) "
              "for their events. Update to stop it.")


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, batch: list[dict]) -> None:
        self.events.extend(batch)


@pytest.fixture(autouse=True)
def real_user(monkeypatch):
    monkeypatch.setenv("MDPIPELINE_INTERNAL", "0")


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def old_store(app_data, **content) -> None:
    (app_data.dir / "usage.json").write_text(json.dumps(content), encoding="utf-8")


# --- the view (UI) ---------------------------------------------------------------------------------------------------------
def test_the_view_carries_the_card_line_and_the_drawer_item_for_a_new_install():
    view = UsageStore().view()
    assert view["counter_eligible"] is True and view["counter_card_line"] == CARD_LINE
    assert view["counter_drawer"]["lead"] == "Sent once, whichever you choose:"
    assert view["counter_drawer"]["json"] == '{"event": "consent_outcome", "outcome": "share" or "decline", "is_internal": false}'
    assert view["counter_drawer"]["rest"] == ("with a fixed ID that is the same for everyone. (`is_internal` is true only on our own test computers.) "
                                              "Nothing else, and never again.")


def test_the_summary_the_home_page_loads_says_whether_the_count_applies():
    from mdpipeline.uiapi import usage_summary

    assert usage_summary()["counter_eligible"] is True


def test_an_old_install_is_not_told_about_a_count_it_will_never_send(app_data):
    old_store(app_data, consent="unanswered")
    view = UsageStore().view()
    assert view["counter_eligible"] is False and view["counter_card_line"] == "You can skip this. If you don't choose, nothing is shared."


# --- the CLI prompt (D4) -------------------------------------------------------------------------------------------------------
def test_the_command_line_question_carries_the_same_line_before_the_choice():
    text = cli.usage_card()
    assert CARD_LINE in text and "Change it any time with `mdpipeline usage on|off`" in text


def test_the_command_line_question_of_an_old_install_keeps_the_old_line():
    text = cli.usage_card(counter=False)
    assert CARD_LINE not in text and "You can skip this: if you don't choose, nothing is shared." in text


def test_the_usage_help_names_the_first_answer_count(capsys):
    with pytest.raises(SystemExit):
        main(["usage", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert ("It is off until you choose. Your first answer sends one anonymous count of that answer (no install ID); nothing else is sent while it is off,"
            in out)


def test_usage_off_as_the_first_answer_says_the_count_was_sent(capsys, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    _, out, _ = run(capsys, "usage", "off")
    assert "Anonymous usage is off. One anonymous count of this answer was sent (no install ID). Nothing else will be." in out


def test_usage_off_later_keeps_the_current_text(capsys, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    run(capsys, "usage", "on")
    _, out, _ = run(capsys, "usage", "off")
    assert "Anonymous usage is off. Nothing is sent." in out and "One anonymous count" not in out


def test_usage_off_on_an_old_install_does_not_claim_a_count(capsys, monkeypatch, app_data):
    old_store(app_data, consent="unanswered")
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    _, out, _ = run(capsys, "usage", "off")
    assert "Anonymous usage is off. Nothing is sent." in out and "One anonymous count" not in out


def test_status_after_dont_share_says_nothing_else_is_sent(capsys):
    UsageStore().set_consent("decline")
    _, out, _ = run(capsys, "usage", "status")
    assert "Not sharing (you chose Don't share). Nothing else is sent." in out


def test_status_after_dont_share_on_an_old_install_does_not_imply_a_count_was_sent(capsys, app_data):
    old_store(app_data, consent="decline")
    _, out, _ = run(capsys, "usage", "status")
    assert "Not sharing (you chose Don't share). Nothing is sent." in out and "Nothing else" not in out


def test_the_log_command_labels_the_count(capsys, monkeypatch):
    monkeypatch.setattr(usage, "get_transport", lambda: Recorder())
    run(capsys, "usage", "off")
    _, out, _ = run(capsys, "usage", "log")
    assert "consent_outcome" in out and "one-time count, no install ID" in out


# --- AC6: the IP address, in marketing's words -----------------------------------------------------------------------------------
def test_the_always_sent_note_ends_with_the_ip_sentence_and_names_the_location_flag():
    assert usage.ALWAYS_SENT.endswith(IP_SENTENCE)
    assert "$geoip_disable" in usage.ALWAYS_SENT


# --- docs/what-we-send.md --------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def doc() -> str:
    return (ROOT / "docs/what-we-send.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_first_paragraph_says_the_count_is_sent_whichever_you_choose(doc):
    text = flat(doc)
    assert ('When you first answer the usage question, the app sends **one anonymous count of your answer**: "share" or "don\'t share", plus a yes/no '
            'that is "no" for you (it marks our own test computers). Nothing else (details below). If you choose "Don\'t share", that count is the only '
            'thing ever sent. If you never answer, nothing is sent.') in text
    assert "If you choose \"Don't share\", or never answer, nothing is sent." not in text


def test_the_new_section_follows_what_is_never_sent_and_shows_the_exact_json(doc):
    assert doc.index("## What is never sent") < doc.index("## The one count of your answer (sent whichever you choose)") < doc.index("## Automatic events")
    assert ('{"event": "consent_outcome", "distinct_id": "anonymous-counter", "properties": {"outcome": "decline", "is_internal": false, '
            '"$process_person_profile": false, "$geoip_disable": true}}') in doc
    text = flat(doc)
    for line in ("`$geoip_disable` tells PostHog not to work out a country or city from your address for this request.",
                 "labelled \"one-time count, no install ID\".",
                 "**If you never answer, it is not sent.** If you want no request at all, skip the card.",
                 "Installs from before version 0.1.24 never send it, whether or not they answered. Their choice was made under the old text."):
        assert line in text


def test_the_ip_sentence_is_in_the_automatic_events_intro_and_the_md233_line_follows_it(doc):
    text = flat(doc)
    assert IP_SENTENCE + " " + MD233_LINE in text
    assert text.index("## Automatic events") < text.index(IP_SENTENCE)
    assert "PostHog also stored the IP address" not in text             # the founder dropped the past-IP sentence (MD-233, LR-1111-8 correction)


def test_the_consent_decided_row_says_you_chose_share(doc):
    row = next(r for r in doc.splitlines() if "`consent_decided`" in r)
    assert row.startswith("| You answer the usage question and choose Share |")


def test_the_events_doc_describes_the_counter_as_it_now_works():
    from mdpipeline.usage_docs import render_doc

    text = render_doc()
    assert "is **off by default**" not in text and "`$geoip_disable`" in text and "is_internal" in text
