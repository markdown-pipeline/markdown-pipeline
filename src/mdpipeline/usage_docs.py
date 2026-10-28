"""Generates ``docs/usage-events.md`` (MD-116 AC5) from the allowlist, so the doc for marketing's PostHog funnels cannot drift from the code.

Regenerate with ``uv run python scripts/gen_usage_events_doc.py``; a test fails when the committed file differs from this output.
"""
from __future__ import annotations

import json

from . import usage
from .usage_events import EVENTS, Count, EmailAddress, Enum, EnumList, Flag, Seconds, UserText, Version

GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("A. Activation path and friction (time to value)", (
        "app_opened", "consent_decided", "onboarding_step_viewed", "onboarding_step_completed", "source_add_started", "source_auth_started",
        "source_auth_completed", "source_auth_failed", "source_connected", "target_connected", "dry_run_viewed", "first_sync_completed",
        "error_shown", "help_opened", "session_ended_before_first_sync", "secret_store_used")),
    ("B. Value and habit", (
        "agent_snippet_copied", "agent_read", "first_agent_read", "sync_completed", "sync_failed", "guard_triggered", "guard_resolved",
        "view_mode_opened", "activity_viewed", "item_reason_viewed")),
    ("C. Expansion", ("source_added", "source_toggled", "source_removed", "pipeline_created")),
    ("D. Intent", ("source_scope_edited", "jira_project_pick", "folder_pick", "branch_pick")),
    ("E. Product additions (install, agent, schedule, requests, notices)", (
        "install_completed", "agent_connected", "try_started", "try_completed", "schedule_changed", "source_requested", "feedback_answered", "feedback_shown",
        "notice_shown", "notice_hidden", "app_updated", "background_started")),
]
EXPLICIT_SENDS = ("source_requested", "feedback_answered")

# Fixed example values, so the generated file is stable.
EXAMPLE_INSTALL_ID = "7f3c1a52-0d4e-4b8a-9c21-5e6f0a1b2c3d"
EXAMPLE_EVENT_ID = "0b9c6f1e-2a47-4d35-8f10-6c7e9d2a4b58"
EXAMPLE_TIME = "2026-10-08T09:15:02Z"
EXAMPLE_GLOBALS = {"app_version": "1.2.3", "deployment": "local", "session_id": "9e1d4c7a2b6f4e8d8a3c5b7f1e2d4a6c", "is_internal": False}
GLOBAL_MEANING = {"app_version": "the running version", "deployment": "always `local` for this app",
                  "session_id": "random, new at each launch, never stored across launches",
                  "is_internal": "`true` only for the developers' own installs and test runs, `false` for everyone else. It is a yes/no and says nothing about "
                                 "you. It is set by running with `MDPIPELINE_INTERNAL=1` (kept afterwards; `MDPIPELINE_INTERNAL=0` takes it off)"}


def _allowed(rule) -> str:
    if isinstance(rule, Enum):
        return " / ".join(f"`{v}`" for v in rule.values)
    if isinstance(rule, EnumList):
        return "list of: " + " / ".join(f"`{v}`" for v in rule.values)
    if isinstance(rule, Count):
        return f"whole number, 0 to {rule.max:,}"
    if isinstance(rule, Seconds):
        return f"seconds, 0 to {rule.max:,}, one decimal"
    if isinstance(rule, Flag):
        return "`true` / `false`"
    if isinstance(rule, Version):
        return "a release number such as `0.3.1`"
    if isinstance(rule, UserText):
        return f"words the person typed and pressed Send on, up to {rule.max} characters (explicit sends only; labelled \"goes to the team\")"
    if isinstance(rule, EmailAddress):
        return "an e-mail address the person typed next to \"Email me if we add it\" and pressed Send on (optional; explicit sends only)"
    return "?"


def _payload(name: str) -> str:
    """One event as it goes over the wire to the analytics host (what the live log in Settings shows, plus the constants it discloses)."""
    properties = {**EVENTS[name].example, **EXAMPLE_GLOBALS, "$process_person_profile": False}
    return json.dumps({"event": name, "distinct_id": EXAMPLE_INSTALL_ID, "uuid": EXAMPLE_EVENT_ID, "timestamp": EXAMPLE_TIME,
                       "properties": properties}, indent=2)


def _section(name: str) -> str:
    event = EVENTS[name]
    lines = [f"### `{name}`", "", f"{event.purpose}. Emitted from: {', '.join(event.surfaces)}."]
    if event.ships_with:
        lines += ["", f"Not emitted yet: ships with {event.ships_with}."]
    if name in EXPLICIT_SENDS:
        lines += ["", "**An explicit send:** it goes out even when sharing is off, only when the user presses Send, and the live log labels it "
                      "\"goes to the team\"."]
    if event.props:
        lines += ["", "| Property | Allowed values |", "|---|---|"] + [f"| `{prop}` | {_allowed(rule)} |" for prop, rule in event.props.items()]
    else:
        lines += ["", "No properties."]
    lines += ["", "Example payload as sent:", "", "```json", _payload(name), "```", ""]
    return "\n".join(lines)


def render_doc() -> str:
    head = f"""# Usage events (MD-116, FR-S2)

Generated from `src/mdpipeline/usage_events.py` by `scripts/gen_usage_events_doc.py`. Do not edit by hand: a test fails when this file
differs from the allowlist. For marketing's PostHog funnels (MM-91).

## Rules

- **Consent first.** Nothing is queued or sent unless the user chose "Share usage" (MD-115). Declining, or not choosing, sends nothing.
- **Allowlist.** Only the events and properties below can be sent. Anything else is dropped before it is queued, and a value that is not
  one of the allowed values is dropped too. There is no free-text property on any automatic event. The only words that can leave are the ones
  a person types in a source request or a feedback answer and sends with the Send button (see the two explicit sends below).
- **Never sent:** titles, file or folder names, paths, JQL or any query text, project keys, URLs, site names, repository names, emails,
  tokens, error text.
- **Allowed:** fixed lists we define, whole-number counts, durations, booleans and the random IDs below.

## Transport

Events are queued locally, then sent in batches of 20 to PostHog's US cloud (`https://us.i.posthog.com/batch/`, https only, only that
host) from a background thread or a detached process, so a sync, the UI, the CLI and the MCP server never wait for the network. A failed
batch stays queued; each event carries a unique `uuid`, so a resent duplicate is dropped by the analytics host. The last 50 events sent
are shown exactly as sent in Settings → Privacy & usage and by `mdpipeline usage log`.

## What every event carries

| Property | Meaning |
|---|---|
| `distinct_id` | the random `install_id` (reset any time in Settings or with `mdpipeline usage reset-id`): the only identifier |
""" + "\n".join(f"| `{k}` | {GLOBAL_MEANING[k]} |" for k in usage._globals()) + """
| `$process_person_profile` | always `false`: tells PostHog not to build a person profile |
| `timestamp` | when the event happened (an `agent_read` day count is dated to that day) |
| `uuid` | unique per event |

## Not events of this list

- **`consent_outcome`** (`share` / `decline` / `no_answer`): the identifier-free consent counter. It has no `distinct_id` of the user, no
  time and no other property, is **off by default** until the owner's legal read, and counts only an install's first decision.
- **Filter events** (`filter_*`) ship with the filter UI. **`teammate_added`** ships with team features. Neither is allowed yet.
"""
    body = []
    for title, names in GROUPS:
        body.append(f"\n## {title}\n")
        body.extend(_section(name) for name in names)
    return head + "\n".join(body).rstrip("\n") + "\n"
