"""The guided first run (MD-118): every setting takes today's default, so the person only chooses a source.

This module builds the pipeline draft for a first run from the few things the person typed. The draft goes through the same
preview and create code as the pipeline editor; nothing here writes or reads files.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from . import sample

DEFAULT_JQL = "updated >= -90d ORDER BY updated DESC"
DEFAULT_ROOT = "~/markdown-pipeline"
JIRA_TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"      # a link the person opens; the page itself loads nothing external


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-")


def default_folder(label: str) -> str:
    """Where the files go unless the person changes it: no spaces, easy to type in a terminal."""
    return f"{DEFAULT_ROOT}/{_slug(label) or 'source'}"


def normalise_site(text: str) -> str:
    """``acme``, ``acme.atlassian.net`` or any pasted Jira address -> ``https://acme.atlassian.net``."""
    raw = text.strip()
    if not raw:
        return ""
    host = (urlsplit(raw if "//" in raw else "//" + raw).hostname or "").lower()
    if "." not in host:
        host += ".atlassian.net"
    return f"https://{host}"


def _jira_draft(body: dict) -> tuple[str, dict]:
    site = normalise_site(str(body.get("site", "")))
    label = (urlsplit(site).hostname or "jira").removesuffix(".atlassian.net") + "-jira"
    source = {"type": "jira", "label": "jira", "site": site, "jql": str(body.get("jql") or "").strip() or DEFAULT_JQL,
              "email": str(body.get("email", "")).strip(), "token": str(body.get("token", "")), "pending": body.get("pending")}
    return label, source


def build_draft(body: dict, brain=None) -> dict:
    """The editor's draft for a first run. Only the source (and, if changed, the folder) comes from the person."""
    if body.get("source") == "jira":
        name, source = _jira_draft(body)
    elif body.get("source") == "sample":
        from . import sample

        name, source = sample.NAME, {"path": str(sample.source_dir()), "label": sample.LABEL}
    else:
        path = str(body.get("path", "")).strip()
        name = _slug(os.path.basename(path.rstrip("/"))) or "folder"
        source = {"path": path, "label": name}
    if brain is not None:
        name = unique_name(brain, name)
    if body.get("target") == "github":      # MD-152: the editor's sign-in and repository list; the sign-in is held (``pending``) until the write
        repo = str(body.get("repo") or "").strip()
        return {"name": name, "sources": [source], "destination": {
            "type": "github", "repo": repo, "branch": str(body.get("branch") or "").strip() or "main", "auth": "sign-in", "clone": "",
            "pending": body.get("gh_pending")}}        # not "pending": a Jira source in the same first run holds its own sign-in handle
    dest = str(body.get("dest") or "").strip() or default_folder(name)
    return {"name": name, "sources": [source], "destination": {"type": "directory", "path": os.path.expanduser(dest)}}


SAMPLE_READ_ONLY = "Sample data: a made-up engineering team, bundled with the app. No sign-in, nothing is sent or read from your accounts."
FIRST_N = 5
EXAMPLE_LINES = 30


def _title(source: str, kind: str) -> str:
    """What the person sees for one item: the file path, or for Jira ``KEY Summary`` (never the project folder or ``.md``)."""
    if kind != "jira":
        return source
    return os.path.basename(source).removesuffix(".md")


def _example(draft: dict, kind: str, first_source: str) -> dict | None:
    """The first item as the agent will get it, for the review screen only (never sent anywhere)."""
    import time

    from .frontmatter import build_provenance, merge_provenance
    from .planner import default_scans
    from .uieditor import draft_to_pipeline

    pipeline = draft_to_pipeline(draft)
    try:
        for scan in default_scans(pipeline):
            for entry in scan.entries:
                if entry.status == "candidate" and (entry.relpath == first_source or entry.identity == first_source):
                    data = entry.read_bytes()
                    prov = build_provenance(pipeline=draft["name"], source_label=scan.label, source_path=entry.identity, source_root=scan.path,
                                            source_mtime=entry.mtime, synced_at=time.time(), source_bytes=data,
                                            source_type="local" if scan.kind == "folder" else scan.kind, extra=entry.provenance or None)
                    text = merge_provenance(data, prov).decode("utf-8", "replace")      # as it will be written, provenance block included
                    return {"name": _title(first_source, kind) if kind == "jira" else first_source,
                            "text": "\n".join(text.splitlines()[:EXAMPLE_LINES])}
    except Exception:           # an example is a nicety: the review stands without it
        return None
    return None


def can_create(path: str) -> str:
    """"" when the folder exists or can be created under an existing, writable parent; otherwise the reason in plain words."""
    probe = os.path.abspath(path)
    while not os.path.lexists(probe):
        probe = os.path.dirname(probe)
    if not os.path.isdir(probe):
        return f"{probe} is a file, so the folder cannot be created there."
    if not os.access(probe, os.W_OK | os.X_OK):
        return f"We can't write in {probe}. Choose another folder."
    return ""


def dest_check(body: dict) -> dict:
    """MD-159: the suggested folder for this source and whether the typed one (or the suggestion) can be used. Nothing is written."""
    draft = build_draft({**body, "dest": "", "target": "directory"})
    suggested = default_folder(draft["name"])
    typed = str(body.get("dest") or "").strip()
    path = os.path.abspath(os.path.expanduser(typed or suggested))
    reason = can_create(path)
    return {"ok": not reason, "suggested": suggested, "path": path, "exists": os.path.isdir(path), "reason": reason}


def _steps(body: dict) -> tuple[str, str]:
    """(noun, source name) for the progress sentence of this first run."""
    return ("issues", "Jira") if body.get("source") == "jira" else ("files", "")


def review(brain, body: dict) -> dict:
    """What the first sync would write, computed without writing anything. ``ok`` is False with the reasons when it cannot.

    Shallow (MD-153): it lists the scope and reads one example, it does not fetch every body. Progress is reported for the screen."""
    from . import progress

    noun, source = _steps(body)
    progress.start("listing", noun=noun, source=source)
    try:
        return _review(brain, body)
    finally:
        progress.finish()


def _review(brain, body: dict) -> dict:
    import tempfile

    from .uieditor import preview

    is_sample = body.get("source") == "sample"
    if is_sample and sample.find(brain) is not None:
        return {"ok": False, "errors": [{"field": "source", "message": "The sample is already set up. Remove it first to start it again."}]}
    draft = build_draft(body, brain)
    kind = "jira" if body.get("source") == "jira" else "folder"
    github = draft["destination"]["type"] == "github"
    dest = "" if github else draft["destination"]["path"]
    if github:                                   # MD-152: the repository is read with the held sign-in; nothing is committed by a review
        result = preview(brain, {"draft": draft, "shallow": True})
        problem = result.get("target_error") or ({"message": "Sign in with GitHub first, then choose a repository."} if result.get("target_pending") else None)
        if problem:
            return {"ok": False, "errors": [{"field": "destination", "message": problem["message"]}]}
    elif not os.path.exists(dest):               # a new folder is created by the write; the plan runs against an empty stand-in
        problem = can_create(dest)
        if problem:
            return {"ok": False, "errors": [{"field": "destination", "message": problem}]}
        with tempfile.TemporaryDirectory(prefix="mdp-review-") as stand_in:
            result = preview(brain, {"draft": {**draft, "destination": {"type": "directory", "path": stand_in}}, "shallow": True})
    else:
        result = preview(brain, {"draft": draft, "shallow": True})
    errors = result["validation"]["errors"]
    if errors:
        return {"ok": False, "errors": [{"field": e.get("field", ""), "message": e["message"]} for e in errors]}
    broken = [g for g in result["preview"]["groups"] if g["status"] != "ok"]
    if broken:                                   # the source itself cannot be read (missing folder, wrong site...): say why
        return {"ok": False, "errors": [{"field": "source", "message": g["error"] or "This source cannot be read."} for g in broken]}
    items = [i for g in result["preview"]["groups"] for i in g["items"] if i["op"] == "create"]
    shown = [_title(i["source"], kind) for i in items[:FIRST_N]]
    out = {"ok": True, "pipeline": draft["name"], "noun": "issues" if kind == "jira" else "files", "count": len(items),
           "first": shown, "paths": [i["target"] for i in items[:FIRST_N]], "more": max(0, len(items) - FIRST_N),
           "dest": {"repo": draft["destination"]["repo"], "branch": draft["destination"]["branch"], "path": "", "exists": True} if github else {"path": dest, "exists": os.path.exists(dest)},
           "target": "github" if github else "directory",
           "read_only": SAMPLE_READ_ONLY if is_sample else "Read-only: we never change anything in " + ("Jira." if kind == "jira" else "this folder."),
           "example": _example(draft, kind, items[0]["source"]) if items else None}
    if kind == "jira":
        out["jql"] = draft["sources"][0]["jql"]
    if is_sample:
        out["sample"] = True
    return out


def unique_name(brain, name: str) -> str:
    """``name``, or ``name-2``, ``name-3``... when a pipeline already has it: the person is never asked to name anything."""
    from .config import load_config

    taken = {p.name for p in load_config(brain).pipelines}
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}-{n}", n + 1
    return candidate


def write(brain, body: dict, sync_fn=None) -> dict:
    """The one click: create the folder and the pipeline with today's defaults and run the first sync."""
    from . import progress

    checked = review(brain, body)
    if not checked["ok"]:
        return checked
    noun, source = _steps(body)
    progress.start("reading", noun=noun, source=source)
    try:
        return _write(brain, body, sync_fn, checked)
    finally:
        progress.finish()


def _write(brain, body: dict, sync_fn, checked: dict) -> dict:
    import time

    from .schedule import next_sync
    from .uieditor import create, draft_to_pipeline

    draft = build_draft(body, brain)
    draft_is_sample = body.get("source") == "sample"
    github = draft["destination"]["type"] == "github"
    if not github:
        os.makedirs(draft["destination"]["path"], exist_ok=True)
    made = create(brain, {"draft": draft, "sync": True}, sync_fn=sync_fn)
    run = made.get("sync") or {}
    now = time.time()
    clear_progress()
    from .scheduler import acknowledge_announcement

    if draft_is_sample:
        from .service import set_schedule

        set_schedule(brain, draft["name"], "off", trigger="ui")      # a bundled sample never changes: nothing to keep current
    acknowledge_announcement()       # the Review and Ready screens already say it; Home must not announce it a second time
    where = ({"target": "github", "folder": "", "repo": draft["destination"]["repo"], "branch": draft["destination"]["branch"],
              "repo_url": f"https://github.com/{draft['destination']['repo']}"} if github else {"target": "directory", "folder": draft["destination"]["path"]})
    return {"ok": True, "pipeline": draft["name"], **where, "result": run.get("result", ""),
            "files": (run.get("counts") or {}).get("created", 0), "errors": run.get("errors", []), "message": run.get("message", ""), "warnings": made.get("warnings", []),
            "schedule": "off" if draft_is_sample else draft_to_pipeline(draft).schedule,
            "next_sync": None if draft_is_sample else next_sync(draft_to_pipeline(draft), now, now), **({"sample": True} if draft_is_sample else {})}


# --- resume (MD-118 AC5): where the person stopped, kept on this computer --------------------------------------
PROGRESS_KEYS = ("step", "source", "site", "email", "path", "dest")          # the token is deliberately not here


def progress_path():
    from .vault import data_dir

    return data_dir() / "firstrun.json"


def read_progress() -> dict | None:
    import json

    try:
        data = json.loads(progress_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: data[k] for k in PROGRESS_KEYS if isinstance(data, dict) and isinstance(data.get(k), str)} or None


def save_progress(body: dict) -> dict | None:
    """Remember the step and the typed values (not the token). ``{"clear": true}`` forgets them ("Start over")."""
    import json

    if body.get("clear"):
        clear_progress()
        return None
    kept = {k: body[k] for k in PROGRESS_KEYS if isinstance(body.get(k), str)}
    path = progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept), encoding="utf-8")
    return kept


def clear_progress() -> None:
    try:
        progress_path().unlink()
    except OSError:
        pass


def open_folder(brain, pipeline: str, opener=None) -> dict:
    """Open the folder a pipeline writes to in the system file manager. Only a known pipeline's own destination can be opened."""
    from .config import load_config
    from .destinations import directory_root
    from .uiapi import os_open

    p = load_config(brain).get(pipeline)
    if p is None:
        raise KeyError(pipeline)
    folder = str(directory_root(brain, p.destination))
    try:
        (opener or os_open)(folder)
    except OSError as exc:
        return {"ok": False, "folder": folder, "error": str(exc)}
    return {"ok": True, "folder": folder}


# --- the guided Jira step (MD-119) ----------------------------------------------------------------------------------
HELP_PATH = "/jira-token-help.md"
# MD-150: "can this account see any project?" is asked of the project list, never of an issue query: real Jira Cloud refuses
# `updated >= -36500d` (HTTP 400) and that used to read as "no project".


def _failure(category: str, message: str, host: str, user: str = "") -> dict:
    """One specific, actionable message for a failed Jira check. Plain words from a fixed set: never the server's text, never a secret."""
    if category == "auth":                      # Atlassian answers 401 for a wrong email and for a bad token alike: one combined message
        return {"kind": "auth", "field": "credentials", "headline": "Atlassian didn't accept this email and token together.",
                "fix": "Check that the email is the one of the Atlassian account that created the token, that the token was copied whole and hasn't expired."}
    if category == "site-not-found":
        return {"kind": "site", "field": "site", "headline": f"We couldn't find a Jira site at {host}.",
                "fix": "Use the part before .atlassian.net in the address you open Jira with. You can also paste the full address."}
    if category == "bad-jira-site":
        return {"kind": "data_center", "field": "site", "headline": "This looks like Jira Data Center or Server, not Jira Cloud.",
                "fix": "Early access supports Jira Cloud only."}
    if category == "network" and message.startswith("Could not reach"):
        return {"kind": "offline", "field": "none", "headline": "No internet connection, so we can't reach Atlassian.",
                "fix": "Check your connection and press Test again. Nothing was saved."}
    if category == "network":
        return {"kind": "unavailable", "field": "none", "headline": "Atlassian is not available right now.",
                "fix": "Try again in a few minutes. Nothing was saved."}
    if category == "rate-limited":
        return {"kind": "unavailable", "field": "none", "headline": "Atlassian is asking us to slow down.",
                "fix": "Wait a minute and press Test again. Nothing was saved."}
    if category == "probe-rejected":
        return {"kind": "other", "field": "none", "headline": "We couldn't check this Jira connection.",
                "fix": "Press Test again. If it keeps failing, tell us with the feedback button: this is a problem on our side, not your account. Nothing was saved."}
    if category == "no_projects" or category == "permission":
        who = f"Signed in as {user}, but this account" if user else "This account"
        return {"kind": "no_projects", "field": "credentials", "headline": f"{who} can't see any Jira project on {host}.",
                "fix": 'Ask a Jira admin for "Browse projects" access, or use another account.'}
    return {"kind": "other", "field": "none", "headline": "We couldn't check this Jira connection.", "fix": message}


def jira_test(body: dict) -> dict:
    """Test and continue: the site, the email and the token, then whether the account can see any project (the project list)."""
    import json

    from . import usage_hooks
    from .credentials import CredentialError
    from .jira_api import JiraApi
    from .sources import SourceError
    from .targetsetup import hold_token
    from .uijira import typed_values
    from .vault import readiness as vault_readiness

    site = normalise_site(str(body.get("site", "")))
    host = (urlsplit(site).hostname or "")
    email, token = typed_values({"email": str(body.get("email", "")), "token": str(body.get("token", "")), "pending": body.get("pending")})

    def failed(category: str, message: str = "", user: str = "") -> dict:
        usage_hooks.jira_checked(False, category)
        return {"ok": False, **_failure(category, message, host, user), "help": HELP_PATH}

    if not email or not token:
        return failed("auth")
    try:
        api = JiraApi(site, email, token)
        me = api.myself()
        try:
            visible = api.projects(max_results=1)["values"]
        except SourceError as exc:
            if exc.category != "permission":
                raise
            visible = [{"unknown": True}]          # a token that may not list projects (MD-141): not "no access"; the picker explains it calmly
    except ValueError:
        return failed("bad-jira-site")
    except (SourceError, CredentialError) as exc:
        category = exc.category if isinstance(exc, SourceError) else "auth"
        return failed("probe-rejected" if category == "bad-query" else category, str(exc))
    user = me.get("displayName") or "unknown user"
    if not visible:
        return failed("no_projects", "", user)
    usage_hooks.jira_checked(True)
    return {"ok": True, "user": user, "site": api.site, "pending": hold_token(json.dumps({"email": email, "token": token})),
            "vault": vault_readiness()}