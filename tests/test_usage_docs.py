"""MD-116 (FR-S2 AC5): the developer doc lists every event with one example payload, and cannot drift from the allowlist."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mdpipeline import usage
from mdpipeline.usage_docs import GROUPS, render_doc
from mdpipeline.usage_events import EVENTS, sanitize

DOC = Path(__file__).parent.parent / "docs" / "usage-events.md"


def test_the_committed_doc_is_exactly_what_the_allowlist_generates():
    assert DOC.read_text(encoding="utf-8") == render_doc(), "regenerate with: uv run python scripts/gen_usage_events_doc.py"


def test_every_event_is_in_exactly_one_group():
    grouped = [name for _, names in GROUPS for name in names]
    assert sorted(grouped) == sorted(EVENTS) and len(grouped) == len(set(grouped))


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_every_event_has_a_section_with_one_valid_example_payload(name):
    text = render_doc()
    section = text.split(f"### `{name}`\n", 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
    payload = json.loads(re.search(r"```json\n(.*?)\n```", section, re.S).group(1))
    assert payload["event"] == name and payload["distinct_id"] and payload["uuid"]
    props = payload["properties"]
    assert props["$process_person_profile"] is False and props["deployment"] == "local" and props["app_version"] and props["session_id"] and props["is_internal"] is False     # the doc's example is a real user's
    own = {k: v for k, v in props.items() if k not in ("$process_person_profile", "deployment", "app_version", "session_id", "is_internal")}
    assert own == EVENTS[name].example and sanitize(name, own, explicit=EVENTS[name].explicit) == own


def test_the_doc_states_the_rules_the_transport_and_which_events_are_explicit_sends():
    text = render_doc()
    for needle in ("Never sent", "install_id", "us.i.posthog.com", "$process_person_profile", "goes out even when sharing is off",
                   "consent_outcome", "Filter events", "teammate_added"):
        assert needle in text, needle
    assert text.count("goes out even when sharing is off") >= 2           # source_requested and feedback_answered


def test_events_that_are_not_emitted_yet_say_which_story_ships_them():
    text = render_doc()
    for name, event in EVENTS.items():
        section = text.split(f"### `{name}`\n", 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
        assert (f"Not emitted yet: ships with {event.ships_with}" in section) == bool(event.ships_with), name


def test_the_doc_lists_the_global_properties_the_app_really_adds():
    text = render_doc()
    for name in usage._globals():
        assert f"`{name}`" in text
