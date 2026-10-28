"""The allowlist of usage events (MD-116 / FR-S2): every event and every property the app may ever send.

Anything not defined here is dropped before it is queued, so nothing can leave by accident. Allowed values are only: fixed enums we
define, whole-number counts, durations, booleans and a version number. There is no "string" rule on purpose: a title, a path, a JQL
text or an error message cannot pass, whatever the calling code passes in.

Spec: marketing's taxonomy (docs/marketing/2026-10-07_source-filtering-usage-reporting-consultation.md §3.1-3.2 A-C, plus
``source_scope_edited`` from D), product's additions (docs/product/2026-10-07_first-release-scope-and-stories.md §6) and product's
comment on MD-116. Filter events (§3.2 D) ship with the filter UI and team events with team features: neither is here.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


# --- value rules -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Enum:
    """One of a fixed list of values we define."""

    values: tuple[str, ...]

    def check(self, value):
        return value if isinstance(value, str) and value in self.values else None


@dataclass(frozen=True)
class EnumList:
    """A list of values from a fixed list; unlisted items are dropped, duplicates removed, order made stable."""

    values: tuple[str, ...]
    max_len: int = 12

    def check(self, value):
        if not isinstance(value, (list, tuple)):
            return None
        kept = sorted({v for v in value if isinstance(v, str) and v in self.values})
        return kept[: self.max_len] if kept else None


@dataclass(frozen=True)
class Count:
    """A whole number, 0..max (a bool is not a number here)."""

    max: int = 1_000_000

    def check(self, value):
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= self.max else None


@dataclass(frozen=True)
class Seconds:
    """A finite, non-negative duration in seconds, rounded to one decimal."""

    max: int = 1_000_000

    def check(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= self.max:
            return None
        return value if isinstance(value, int) else round(value, 1)


@dataclass(frozen=True)
class Flag:
    def check(self, value):
        return value if isinstance(value, bool) else None


@dataclass(frozen=True)
class Version:
    """A release number such as 0.3.1."""

    def check(self, value):
        return value if isinstance(value, str) and re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", value) else None


@dataclass(frozen=True)
class UserText:
    """Words the person typed and pressed Send on (MD-123). Valid only on an ``explicit`` event: emit() can never produce it.

    Trimmed, no control characters (a newline is fine), cut to ``max`` characters; empty or non-text is dropped.
    """

    max: int = 500

    def check(self, value):
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or any(ord(c) < 32 and c not in "\n\t" for c in text):
            return None
        return text[: self.max]


@dataclass(frozen=True)
class EmailAddress:
    """An optional e-mail the person typed next to "Email me if we add it" (MD-123). Explicit events only."""

    def check(self, value):
        return value.strip() if isinstance(value, str) and len(value.strip()) <= 120 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s.]+", value.strip()) else None


MS = Count(max=3_000_000_000)          # a duration in milliseconds, up to about a month

# --- the fixed lists ---------------------------------------------------------------------------------------------------
SOURCE_TYPE = Enum(("folder", "jira", "other"))
TARGET_TYPE = Enum(("folder", "github"))
ENTRY_POINT = Enum(("empty_state", "editor", "onboarding", "home", "activity", "view", "settings", "other"))
AUTH_METHOD = Enum(("token", "oauth"))
ERROR_CLASS = Enum(("auth", "rate_limit", "network", "guard_blocked", "target_write", "validation", "probe_rejected", "unknown"))
UI_AREA = Enum(("home", "editor", "source", "target", "preview", "activity", "view", "settings", "onboarding", "other"))
STEP = Enum(("consent", "choose_source", "authorize", "choose_scope", "choose_target", "preview", "first_sync", "connect_agent"))
BUCKET = Enum(("0", "1-10", "11-100", "101-1000", "1000+"))
AGENT = Enum(("claude_code", "codex", "cursor", "other"))
METHOD = Enum(("auto", "manual"))
TRIGGER = Enum(("schedule", "manual"))
GUARD_CAUSE = Enum(("filter_change", "scope_change", "source_change", "unknown"))
GUARD_ACTION = Enum(("approved", "cancelled"))
HELP_TOPIC = Enum(("jira_token", "github_signin", "big_change_guard", "target", "agent_connect", "other"))
ITEM_REASON = Enum(("filtered", "waiting_to_settle", "deleted_in_source"))
ON_OFF = Enum(("on", "off"))
AGE_BUCKET = Enum(("<1h", "<1d", "<7d", ">=7d"))
INTERVAL = Enum(("off", "15m", "1h", "6h", "daily"))
SOURCE_ID = Enum(("confluence", "notion", "google_drive", "github_issues", "linear", "slack", "sharepoint", "jira_data_center", "other"))
QUESTION_ID = Enum(("first_sync_expectation", "no_agent_read_3d", "last_source_removed", "stopped_before_first_sync"))
ANSWER = Enum(("yes", "partly", "no", "other",                                                    # first sync: did you get what you expected
               "no_agent_installed", "dont_know_how", "tried_failed", "no_time", "files_not_useful",     # three days without an agent read
               "just_testing", "wrong_source", "not_useful", "privacy_worry", "broke",                  # removing the last source
               "reads_folder_directly",                                                                 # MD-168 M1: an agent that reads the folder, not through us
               "sign_in_trouble", "step_unclear", "source_missing", "just_looking"))                                    # MD-168 M3: what stopped the first run (no_time/privacy_worry/broke/other are shared)
NOTICE_KIND = Enum(("sync_failed", "source_missing", "stale", "guard_blocked", "vault_locked", "vault_unsafe", "linger"))
NOTICE_SURFACE = Enum(("ready", "home", "settings", "cli"))                       # MD-168 M4: where the linger line was shown (a screen name, never content)
LINGER_KIND = Enum(("linger",))
YES_NO_UNKNOWN = Enum(("yes", "no", "unknown"))
SECRET_STORE = Enum(("keychain", "file"))
BACKGROUND_HOW = Enum(("login_item", "user_service", "app_start", "manual"))     # MD-165: how the background process was started
OS_NAME = Enum(("macos", "linux", "windows", "other"))
INSTALL_METHOD = Enum(("script", "pip", "pipx", "uv", "source", "other"))
SCOPE_KIND = Enum(("jql", "glob", "project"))
FOLDER_METHOD = Enum(("browse", "typed", "recent"))
BRANCH_METHOD = Enum(("picker", "typed"))
TARGET = Enum(("directory", "github"))           # MD-152: where the first run writes (a kind, never a repository name)
PICK_METHOD = Enum(("picker", "advanced_jql"))
QUERY_FIELDS = EnumList(("status", "priority", "issuetype", "updated", "created", "labels", "component", "assignee", "project", "sprint", "custom"))


@dataclass(frozen=True)
class Event:
    purpose: str                                  # the question the event answers (for the developer doc and marketing's funnels)
    surfaces: tuple[str, ...]                     # where it is emitted: ui, cli, mcp, engine
    props: dict = field(default_factory=dict)     # property name -> rule
    example: dict = field(default_factory=dict)   # one valid payload (must use every property)
    ships_with: str = ""                          # empty = emitted now; else the story whose feature emits it
    explicit: bool = False                        # built only from something the person pressed Send on; may carry typed words (MD-123)


EVENTS: dict[str, Event] = {
    # --- A. activation path and friction (R3-R4, time to value) ---
    "app_opened": Event("Start of the time-to-value clock", ("ui", "cli"), {"first_open": Flag()}, {"first_open": True}),
    "consent_decided": Event("Consent rate (only a 'share' can be sent: a decline sends nothing, it is only counted by the opt-in counter)",
                             ("ui", "cli"), {"choice": Enum(("share",))}, {"choice": "share"}),
    "onboarding_step_viewed": Event("Where people stop (the key funnel)", ("ui",), {"step": STEP}, {"step": "choose_source"}),
    "onboarding_step_completed": Event("Where people stop (the key funnel); on the where-files-go step also whether the first run writes to a folder or a GitHub repo, and whether the suggested folder was edited (MD-152, MD-159, H-TGT-1, H-DEST-1)",
                                       ("ui",), {"step": STEP, "ms_on_step": MS, "target": TARGET, "dest_changed": Flag()},
                                       {"step": "choose_target", "ms_on_step": 21000, "target": "directory", "dest_changed": False}),
    "secret_store_used": Event("Which store keeps the person's secrets: the OS keychain or the user-only file when there is none (MD-163, H-KEY-1: the share of users without a keychain); no content",
                               ("ui",), {"store": SECRET_STORE}, {"store": "file"}),
    "background_started": Event("How the background process got started: the login item, the systemd user service, the app's own restart, or by hand (MD-165, H-HEADLESS-1: how many installs have no desktop login); whether a desktop session exists and whether systemd lingering is on (MD-168, yes/no/unknown, read only); no content",
                                ("cli",), {"how": BACKGROUND_HOW, "desktop_session": YES_NO_UNKNOWN, "linger_enabled": YES_NO_UNKNOWN},
                                {"how": "user_service", "desktop_session": "no", "linger_enabled": "no"}),
    "source_add_started": Event("Which source people reach for first", ("ui",), {"source_type": SOURCE_TYPE, "entry_point": ENTRY_POINT},
                                {"source_type": "jira", "entry_point": "empty_state"}),
    "source_auth_started": Event("Credential friction", ("ui", "cli"), {"source_type": SOURCE_TYPE, "auth_method": AUTH_METHOD},
                                 {"source_type": "jira", "auth_method": "token"}),
    "source_auth_completed": Event("Credential friction", ("ui", "cli"), {"source_type": SOURCE_TYPE, "auth_method": AUTH_METHOD},
                                   {"source_type": "jira", "auth_method": "token"}),
    "source_auth_failed": Event("Credential friction", ("ui", "cli"),
                                {"source_type": SOURCE_TYPE, "auth_method": AUTH_METHOD, "error_class": ERROR_CLASS},
                                {"source_type": "jira", "auth_method": "token", "error_class": "auth"}),
    "source_connected": Event("R4: a source is connected", ("ui", "cli"), {"source_type": SOURCE_TYPE}, {"source_type": "jira"}),
    "target_connected": Event("Destination preference", ("ui", "cli"), {"target_type": TARGET_TYPE}, {"target_type": "github"}),
    "dry_run_viewed": Event("Use of the trust preview", ("ui", "cli"), {"items_bucket": BUCKET, "would_delete_bucket": BUCKET},
                            {"items_bucket": "11-100", "would_delete_bucket": "0"}),
    "first_sync_completed": Event("Time to first file", ("engine",),
                                  {"source_type": SOURCE_TYPE, "files_bucket": BUCKET, "duration_s": Seconds(), "ms_since_first_open": MS},
                                  {"source_type": "jira", "files_bucket": "11-100", "duration_s": 12.4, "ms_since_first_open": 241000}),
    "error_shown": Event("Friction hotspots", ("ui",), {"ui_area": UI_AREA, "error_class": ERROR_CLASS},
                         {"ui_area": "source", "error_class": "auth"}),
    "help_opened": Event("Where users get confused", ("ui",), {"topic_id": HELP_TOPIC}, {"topic_id": "jira_token"}),
    "session_ended_before_first_sync": Event("Abandonment point", ("ui",), {"last_step": STEP}, {"last_step": "authorize"}),
    # --- B. value and habit (R4+ to R5) ---
    "agent_snippet_copied": Event("The aha step was taken", ("ui",), {"agent": AGENT}, {"agent": "claude_code"}),
    "agent_read": Event("R4+ aha and R5 habit: reads through MCP per day (a count, nothing else)", ("mcp",), {"reads_count": Count()},
                        {"reads_count": 7}),
    "first_agent_read": Event("Did the agent ever read the files, and how long after the first sync", ("mcp",), {"ms_since_first_sync": MS},
                              {"ms_since_first_sync": 340000}),
    "sync_completed": Event("'Always current' works", ("engine",),
                            {"trigger": TRIGGER, "source_type": SOURCE_TYPE, "changed_bucket": BUCKET, "deleted_bucket": BUCKET,
                             "duration_s": Seconds()},
                            {"trigger": "manual", "source_type": "jira", "changed_bucket": "1-10", "deleted_bucket": "0", "duration_s": 3.2}),
    "sync_failed": Event("Reliability as users see it", ("engine",), {"error_class": ERROR_CLASS, "source_type": SOURCE_TYPE},
                         {"error_class": "network", "source_type": "jira"}),
    "guard_triggered": Event("Big-change friction", ("engine",), {"cause": GUARD_CAUSE}, {"cause": "unknown"}),
    "guard_resolved": Event("Big-change friction", ("ui", "cli"), {"cause": GUARD_CAUSE, "action": GUARD_ACTION},
                            {"cause": "unknown", "action": "approved"}),
    "view_mode_opened": Event("Do users check state", ("ui",), {"entry_point": ENTRY_POINT}, {"entry_point": "home"}),
    "activity_viewed": Event("Do users check state", ("ui",), {"entry_point": ENTRY_POINT}, {"entry_point": "home"}),
    "item_reason_viewed": Event("'Why is my file missing?' volume", ("ui",), {"reason": ITEM_REASON}, {"reason": "deleted_in_source"}, "MD-122"),
    # --- C. expansion (R6) ---
    "source_added": Event("Expansion", ("ui", "cli"), {"source_type": SOURCE_TYPE, "n_sources": Count(1000)},
                          {"source_type": "folder", "n_sources": 2}),
    "source_toggled": Event("Expansion", ("ui", "cli"), {"state": ON_OFF}, {"state": "off"}),
    "source_removed": Event("Expansion", ("ui", "cli"), {"age_bucket": AGE_BUCKET}, {"age_bucket": "<7d"}),
    "pipeline_created": Event("Expansion", ("ui", "cli"), {"n_pipelines": Count(1000)}, {"n_pipelines": 1}),
    # --- D. the one intent event instrumented now ---
    "source_scope_edited": Event("Do users narrow scope today? (cheapest demand evidence)", ("ui", "cli"),
                                 {"source_type": SOURCE_TYPE, "kind": SCOPE_KIND, "fields_used": QUERY_FIELDS, "clause_count": Count(1000),
                                  "pattern_count": Count(1000), "uses_order_by": Flag()},
                                 {"source_type": "jira", "kind": "jql", "fields_used": ["status", "updated"], "clause_count": 2,
                                  "pattern_count": 0, "uses_order_by": True}),
    "jira_project_pick": Event("Does the project picker cut the Jira step? (MD-141: method and a bucket, never a key, name or query)", ("ui",),
                               {"method": PICK_METHOD, "count_bucket": BUCKET}, {"method": "picker", "count_bucket": "1-10"}),
    "folder_pick": Event("Do people browse for a folder or type it? (MD-142: a method, never a path or folder name)", ("ui",),
                         {"method": FOLDER_METHOD}, {"method": "browse"}),
    "branch_pick": Event("Do people choose a GitHub branch from the list or type it? (MD-143: a method, never a branch name)", ("ui",),
                         {"method": BRANCH_METHOD}, {"method": "picker"}),
    # --- product additions (spec §6) ---
    "install_completed": Event("Install funnel", ("cli",), {"os": OS_NAME, "install_method": INSTALL_METHOD},
                               {"os": "macos", "install_method": "script"}),
    "agent_connected": Event("The aha step: the agent is connected", ("ui", "cli"), {"agent": AGENT, "method": METHOD},
                             {"agent": "claude_code", "method": "auto"}),
    "try_started": Event("Taste with sample data", ("ui", "cli"), {}, {}),
    "try_completed": Event("Taste with sample data", ("ui", "cli"), {"duration_s": Seconds()}, {"duration_s": 4.5}),
    "schedule_changed": Event("Is scheduled sync kept on", ("ui", "cli"), {"old_interval": INTERVAL, "new_interval": INTERVAL},
                              {"old_interval": "1h", "new_interval": "daily"}),
    "source_requested": Event("Source demand (an explicit send: goes out even with sharing off)", ("ui",),
                              {"source_id": SOURCE_ID, "other_source": UserText(100), "email": EmailAddress()},
                              {"source_id": "other", "other_source": "Linear", "email": "dana@acme.dev"}, explicit=True),
    "feedback_answered": Event("The why behind drop-offs (an explicit send)", ("ui",),
                               {"question_id": QUESTION_ID, "answer": ANSWER, "comment": UserText(500)},
                               {"question_id": "first_sync_expectation", "answer": "partly", "comment": "Missing the comments"}, explicit=True),
    "feedback_shown": Event("How many times each one-question card was shown, so the answer rate can be read (MD-172): only which question, never its text",
                            ("ui",), {"question_id": QUESTION_ID}, {"question_id": "first_sync_expectation"}),
    "notice_shown": Event("Reliability notices users get; for the headless-Linux linger line (kind `linger`, MD-168, H-LINGER-1) also the screen it was shown on, at most once per screen per day",
                          ("ui", "engine", "cli"), {"kind": NOTICE_KIND, "surface": NOTICE_SURFACE}, {"kind": "linger", "surface": "home"}),
    "notice_hidden": Event("The person pressed Hide on the linger line on Home (MD-168, H-LINGER-1); a fixed kind and screen, nothing else",
                           ("ui",), {"kind": LINGER_KIND, "surface": NOTICE_SURFACE}, {"kind": "linger", "surface": "home"}),
    "app_updated": Event("Update adoption", ("ui", "cli"), {"previous_version": Version()}, {"previous_version": "0.3.1"}),
}


def sanitize(name: str, properties: dict | None, *, explicit: bool = False) -> dict | None:
    """The cleaned properties of an allowed event, or None when the event is not on the list.

    Unlisted properties and values that do not pass their rule are dropped (the event itself is kept). The input is never changed.
    ``explicit`` says the caller is an explicit send (the person pressed Send): an explicit event is refused on the automatic path and
    an automatic event on the explicit one, so typed words can only travel through a button the person pressed.
    """
    event = EVENTS.get(name)
    if event is None or event.explicit != explicit:
        return None
    given = properties if isinstance(properties, dict) else {}
    clean = {}
    for prop, rule in event.props.items():
        if prop in given:
            value = rule.check(given[prop])
            if value is not None:
                clean[prop] = value
    return clean
