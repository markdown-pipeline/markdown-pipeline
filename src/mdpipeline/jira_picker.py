"""The Jira project picker (MD-141): list the projects an account can read, and build the JQL from the ticked ones.

Names and keys are shown on this computer only and used to build the query stored in the person's own configuration. Nothing
here returns an email or a token, and nothing here reports a key, name, site or query anywhere (the one usage event carries a
method and a count bucket).
"""
from __future__ import annotations

import json
import re

from . import usage_hooks
from .credentials import CredentialError, resolve_token
from .firstrun import HELP_PATH, _failure, normalise_site
from .jira_api import JiraApi
from .sources import SourceError
from .targetsetup import hold_token
from .uijira import typed_values

ORDER = "ORDER BY updated DESC"
KEY = re.compile(r"[A-Z][A-Z0-9_]{1,9}")           # Jira's own rule for a project key
PAGE = 100


WINDOWS = (30, 90, None)          # MD-159: the time window of the first run's Projects step: 30 days, 90 days, all time


def build_jql(keys: list[str], days: int | None = None, window: bool = False) -> str:
    """``project in (A, B) AND updated >= -30d ORDER BY updated DESC``. A key is never escaped into the text: one that is not a Jira key is refused.

    ``window=True`` (MD-159): the time window is part of the question (``days`` 30, 90 or None for all time) and no project means every project."""
    clean = sorted({str(k) for k in keys})
    if not clean and not window:
        raise ValueError("Pick at least one project.")
    for key in clean:
        if not KEY.fullmatch(key):
            raise ValueError("That is not a Jira project key.")
    if window and days not in WINDOWS:
        raise ValueError("Choose the last 30 days, the last 90 days or all time.")
    parts = ([f"project in ({', '.join(clean)})"] if clean else []) + ([f"updated >= -{days}d"] if window and days else [])
    return " AND ".join(parts) + (" " if parts else "") + ORDER


def plain_line(keys: list[str]) -> str:
    """What the query will read, in words."""
    clean = sorted({str(k) for k in keys})
    names = clean[0] if len(clean) == 1 else f"{', '.join(clean[:-1])} and {clean[-1]}"
    return f"Every issue in {names}, newest change first."


def pick(body: dict) -> dict:
    """The query for the ticked projects. Reports the pick (method and a count bucket only). Raises ``ValueError`` for a bad key."""
    keys = body.get("keys")
    if not isinstance(keys, list):
        raise ValueError("Pick at least one project.")
    window = "days" in body                                    # MD-159: the Projects step always sends its time window (null = all time)
    jql = build_jql(keys, body.get("days"), window)
    if keys:
        usage_hooks.jira_project_picked("picker", len(set(keys)))
    return {"jql": jql, "line": plain_line(keys) if keys else "Every issue you can see" + _window_words(body.get("days")) + ", newest change first."}


def _window_words(days) -> str:
    return f", updated in the last {days} days" if days else ""


def _calm(kind: str, headline: str, fix: str, **extra) -> dict:
    return {"kind": kind, "headline": headline, "fix": fix, "help": HELP_PATH, "raw_jql": True, **extra}


def list_projects(body: dict) -> dict:
    """One page of readable projects, or a calm state that says what happened and keeps raw JQL available. Always 200."""
    site = normalise_site(str(body.get("site", "")))
    email, token = typed_values(body)
    start = max(int(body.get("start_at") or 0), 0)
    try:
        email_ref, token_ref = str(body.get("email_ref") or "").strip(), str(body.get("token_ref") or "").strip()
        api = JiraApi(site, email if email else (lambda: resolve_token(email_ref)), token if token else (lambda: resolve_token(token_ref)))
        page = api.projects(start_at=start, max_results=PAGE, query=str(body.get("query") or "").strip())
    except ValueError:
        return {"ok": False, **_failure("bad-jira-site", "", site), "raw_jql": True}
    except (SourceError, CredentialError) as exc:
        category = exc.category if isinstance(exc, SourceError) else "auth"
        if category == "permission":
            return {"ok": False, **_calm("missing_scope", "This token can't list projects.",
                                         "Create the token with the read:jira-work scope (see the help page), or write your own JQL under Advanced.")}
        return {"ok": False, **_failure(category, str(exc), site), "raw_jql": True}
    projects = [{"key": str(p.get("key", "")), "name": str(p.get("name", ""))} for p in page["values"] if p.get("key")]
    out = {"ok": True, "projects": projects, "is_last": bool(page.get("isLast", True)), "next_start": start + len(page["values"])}
    if email or token:
        out["pending"] = hold_token(json.dumps({"email": email, "token": token}))
    if not projects and start == 0 and not str(body.get("query") or "").strip():
        out.update(_calm("no_projects", "This account can't see any Jira project.", 'Ask a Jira admin for "Browse projects" access, or write your own JQL under Advanced.'))
    return out
