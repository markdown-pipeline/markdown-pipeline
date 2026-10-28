"""The UI's Jira helpers (MD-53, MD-88): Test connection for a source typed in the editor.

The email and API token may be typed (a password field): they go once to this local server, are checked against Jira, and are kept in
the vault when the pipeline is saved. A tested set is held in memory under an opaque handle so the live preview and the save can use it
without the browser sending it again. Nothing here returns an email or a token; saved sources show only references.
"""
from __future__ import annotations

import json

from . import usage_hooks
from .config import Source
from .credentials import CredentialError
from .jirasetup import check_values
from .service import ValidationFailed
from .sources import SourceError
from .targetsetup import hold_token, held_token


def typed_values(s: dict) -> tuple[str | None, str | None]:
    """The typed email and token of a source draft: from its fields, or from a handle of an earlier successful test."""
    email, token = None, None
    held = held_token(s.get("pending"))
    if held:
        data = json.loads(held)
        email, token = data.get("email"), data.get("token")
    return (str(s.get("email") or "").strip() or email), (str(s.get("token") or "") or token)


def test_connection(body: dict) -> dict:
    """Read-only check of the site, the credentials and the JQL. Always 200: ``ok`` says whether it passed."""
    site = str(body.get("site", "")).strip().rstrip("/")
    email, token = typed_values(body)
    source = Source(site, str(body.get("label", "")).strip() or "jira", type="jira", options={
        "site": site, "jql": str(body.get("jql", "")).strip(), "email_ref": str(body.get("email_ref", "")).strip(),
        "token_ref": str(body.get("token_ref", "")).strip()})
    try:
        info = check_values(source, email, token)
    except ValidationFailed as exc:
        errors = [{"category": i.code.removeprefix("jira-") if i.code.startswith("jira-") else i.code, "message": i.message} for i in exc.result.errors]
        usage_hooks.jira_checked(False, errors[0]["category"] if errors else "")
        return {"ok": False, "errors": errors}
    except SourceError as exc:
        usage_hooks.jira_checked(False, exc.category)
        return {"ok": False, "errors": [{"category": exc.category, "message": str(exc)}]}
    except CredentialError as exc:
        usage_hooks.jira_checked(False, "auth")
        return {"ok": False, "errors": [{"category": "auth", "message": str(exc)}]}
    usage_hooks.jira_checked(True)
    out = {"ok": True, "user": info["user"], "matches": info["matches"], "site": info["site"]}
    if email or token:
        out["pending"] = hold_token(json.dumps({"email": email, "token": token}))
    return out
