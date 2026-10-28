"""MD-116 (FR-S2 AC2): the allowlist. Every event and property is defined; anything else is dropped before it can be sent."""
from __future__ import annotations

import math
import re

import pytest

from mdpipeline.usage_events import EVENTS, EmailAddress, UserText, sanitize

CANARY = "ACME-secret-title /home/user/private.md https://acme.atlassian.net a@b.co ghp_abcdef123456"


def test_an_unknown_event_is_dropped():
    assert sanitize("something_new", {"x": 1}) is None


def test_a_known_event_passes_its_listed_properties_through():
    props = {"source_type": "jira", "entry_point": "editor"}
    assert sanitize("source_add_started", props) == props


def test_an_event_without_properties_is_fine():
    assert sanitize("app_opened", {}) == {}
    assert sanitize("app_opened", None) == {}


def test_an_unlisted_property_is_dropped_but_the_event_is_kept():
    assert sanitize("source_add_started", {"source_type": "jira", "title": CANARY}) == {"source_type": "jira"}


@pytest.mark.parametrize("value", [CANARY, "JIRA", "jira ", "", 3, None, True, ["jira"]])
def test_an_enum_value_that_is_not_listed_is_dropped_so_no_free_text_leaves(value):
    assert sanitize("source_add_started", {"source_type": value}) == {}


def test_counts_must_be_whole_non_negative_numbers_within_bounds():
    assert sanitize("agent_read", {"reads_count": 12}) == {"reads_count": 12}
    for bad in (-1, 1.5, True, "12", None, 10**9):
        assert sanitize("agent_read", {"reads_count": bad}) == {}, bad


def test_durations_are_rounded_to_one_decimal_and_must_be_finite_and_not_negative():
    assert sanitize("sync_completed", {"duration_s": 3.14159}) == {"duration_s": 3.1}
    assert sanitize("sync_completed", {"duration_s": 4}) == {"duration_s": 4}
    for bad in (-0.1, math.inf, math.nan, "3", True, 10**7):
        assert sanitize("sync_completed", {"duration_s": bad}) == {}, bad


def test_booleans_must_be_real_booleans():
    assert sanitize("app_opened", {"first_open": True}) == {"first_open": True}
    for bad in ("true", 1, None):
        assert sanitize("app_opened", {"first_open": bad}) == {}


def test_a_list_property_keeps_only_listed_values_without_duplicates_in_a_stable_order():
    out = sanitize("source_scope_edited", {"source_type": "jira", "kind": "jql", "fields_used": ["status", CANARY, "labels", "status"]})
    assert out["fields_used"] == ["labels", "status"]


def test_a_list_is_not_accepted_where_a_single_value_is_expected_and_vice_versa():
    assert sanitize("source_scope_edited", {"fields_used": "status"}) == {}
    assert sanitize("source_add_started", {"source_type": ["jira"]}) == {}


def test_a_version_property_must_look_like_a_version():
    assert sanitize("app_updated", {"previous_version": "0.3.1"}) == {"previous_version": "0.3.1"}
    assert sanitize("app_updated", {"previous_version": CANARY}) == {}


def test_the_input_is_never_modified():
    props = {"source_type": "jira", "title": CANARY}
    sanitize("source_add_started", props)
    assert props == {"source_type": "jira", "title": CANARY}


def test_the_global_properties_are_not_accepted_from_callers():
    for name in ("install_id", "app_version", "deployment", "session_id"):
        assert sanitize("app_opened", {name: "x"}) == {}, name


# --- the catalogue itself --------------------------------------------------------------------------------------------
SPEC_EVENTS = {
    # marketing taxonomy §3.2 A
    "app_opened", "consent_decided", "onboarding_step_viewed", "onboarding_step_completed", "source_add_started", "source_auth_started",
    "source_auth_completed", "source_auth_failed", "source_connected", "target_connected", "dry_run_viewed", "first_sync_completed",
    "error_shown", "help_opened", "session_ended_before_first_sync",
    # B
    "agent_snippet_copied", "agent_read", "sync_completed", "sync_failed", "guard_triggered", "guard_resolved", "view_mode_opened",
    "activity_viewed", "item_reason_viewed",
    # C
    "source_added", "source_toggled", "source_removed", "pipeline_created",
    # D (the one instrumented now)
    "source_scope_edited",
    # product additions and product's comment on MD-116
    "install_completed", "agent_connected", "try_started", "try_completed", "schedule_changed", "source_requested", "feedback_answered",
    "notice_shown", "app_updated", "first_agent_read",
}


def test_every_event_of_the_spec_is_in_the_allowlist():
    assert SPEC_EVENTS <= set(EVENTS)


def test_filter_events_and_team_events_are_not_in_the_first_release_allowlist():
    assert not [n for n in EVENTS if n.startswith("filter_")] and "teammate_added" not in EVENTS


def test_the_first_agent_read_and_schedule_events_carry_the_properties_product_asked_for():
    assert set(EVENTS["first_agent_read"].props) == {"ms_since_first_sync"}
    assert set(EVENTS["schedule_changed"].props) == {"old_interval", "new_interval"}
    assert "ms_since_first_open" in EVENTS["first_sync_completed"].props


def test_onboarding_steps_cover_the_eight_steps():       # MD-159 added choose_scope (the Projects step)
    steps = EVENTS["onboarding_step_viewed"].props["step"].values
    assert set(steps) == {"consent", "choose_source", "authorize", "choose_scope", "choose_target", "preview", "first_sync", "connect_agent"}


FORBIDDEN_NAME = re.compile(r"title|name|path|url|uri|email|token|jql|query|error_text|message|text|repo|site|host|key|label|summary|body", re.I)


def test_no_property_is_named_like_something_that_holds_content():
    # MD-123: the one exception is words the person typed and pressed Send on (explicit events only, checked in test_usage_explicit.py)
    offenders = [(e, p) for e, ev in EVENTS.items() for p, rule in ev.props.items()
                 if FORBIDDEN_NAME.search(p) and not (ev.explicit and isinstance(rule, (UserText, EmailAddress)))]
    assert offenders == []


def test_every_enum_value_is_a_short_slug_never_free_text():
    slug = re.compile(r"^[a-z0-9_<>=.+\-]{1,26}$")         # MD-168: 26, for the question id stopped_before_first_sync (25), fixed by product
    bad = [(e, p, v) for e, ev in EVENTS.items() for p, rule in ev.props.items() for v in getattr(rule, "values", ()) if not slug.match(v)]
    assert bad == []


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_every_example_is_accepted_unchanged_by_the_allowlist(name):
    event = EVENTS[name]
    assert event.purpose and event.surfaces
    assert sanitize(name, event.example, explicit=event.explicit) == event.example


def test_every_event_with_properties_has_an_example_that_uses_each_property():
    for name, event in EVENTS.items():
        assert set(event.example) == set(event.props), name
