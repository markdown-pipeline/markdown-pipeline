"""Read-only description of one saved pipeline (MD-92..95): its configuration and status, for the UI view mode and `pipeline show`.

Nothing here writes: no config, no state, no file in any target. Credentials are described as method + set / not set + name,
never as a value. The functions are pure reads over the brain; the HTTP layer only exposes them through GET routes.
"""
from __future__ import annotations

import datetime
import os

from . import changes
from . import vault as vault_mod
from .brain import Brain
from .config import Pipeline, Source, load_config
from .credentials import CredentialError, parse_ref
from .destinations import credential_conflict_message, credential_conflict_of, describe_destination
from .naming import ORIGINAL, render_file_name
from .service import NotFound
from .uieditor import folder_check

SAMPLE_FILE = "meeting-notes.md"


def credential_view(ref: str) -> dict | None:
    """How a credential reference is shown: its method, whether it is set and its name. Never the value.

    ``set`` is None when it cannot be known without side effects (a locked vault, an OS keychain, which could prompt)."""
    if not ref:
        return None
    try:
        kind, name = parse_ref(ref)
    except CredentialError:
        return {"method": "invalid", "name": "", "set": False}        # a pasted token or junk is never echoed back
    if kind == "env":
        return {"method": "env", "name": name, "set": bool(os.environ.get(name, "").strip())}
    if kind == "keychain":
        return {"method": "keychain", "name": name, "set": None}
    try:
        known = {s.id: s.name for s in vault_mod.default_vault().list()}
    except vault_mod.VaultLocked:
        return {"method": "vault", "name": name, "set": None, "locked": True}
    except vault_mod.VaultError:
        return {"method": "vault", "name": name, "set": None}
    return {"method": "vault", "name": known.get(name, name), "set": name in known}


def name_policy_view(p: Pipeline) -> dict:
    """The file-name policy and one example name (an illustrative file ``meeting-notes.md`` of the first source)."""
    label = p.sources[0].label if p.sources else "source"
    try:
        example = render_file_name(p.name_policy, p.name_pattern, filename=SAMPLE_FILE, first_seen=datetime.date.today().isoformat(),
                                   source=label, pipeline=p.name)
    except (KeyError, ValueError):
        example = None
    return {"policy": p.name_policy or ORIGINAL, "pattern": p.name_pattern, "example": example}


OFF_CHECK = {"ok": True, "exists": None, "md_files": 0, "message": "Off: not read, its files are kept"}


def _folder_source(s: Source) -> dict:
    if not s.enabled:          # an Off source is not read, not even to count its files
        return {"type": "folder", "label": s.label, "path": s.path, "enabled": False, "check": dict(OFF_CHECK)}
    c = folder_check(s.path)
    if not c["exists"]:
        check = {"ok": False, "exists": False, "md_files": 0, "missing": True, "message": "Folder not found"}
    elif not c["is_dir"]:
        check = {"ok": False, "exists": True, "md_files": 0, "message": "Not a folder"}
    else:
        n = c["md_files"]
        check = {"ok": True, "exists": True, "md_files": n, "message": f"Folder found · {n} .md file{'s' if n != 1 else ''}"}
    return {"type": "folder", "label": s.label, "path": s.path, "enabled": True, "check": check}


def _jira_source(s: Source) -> dict:
    o = s.options
    return {"type": "jira", "label": s.label, "enabled": s.enabled, "site": o.get("site", ""), "jql": o.get("jql", ""),
            "credentials": {"email": credential_view(o.get("email_ref", "")), "token": credential_view(o.get("token_ref", ""))},
            "check": None}      # a Jira source is checked by the dry run (one read-only planning pass), not by a second call


def _destination(brain: Brain, p: Pipeline) -> dict:
    out = describe_destination(brain, p)
    if out["type"] == "github":
        out["credential"] = credential_view((p.destination or {}).get("credential", ""))
        from .uitargets import signin_login

        login = signin_login(p)
        if login:
            out["login"] = login["login"]
        clash = credential_conflict_of(brain, load_config(brain), p)
        if clash:         # MD-97: the other pipelines on this repository and branch that name a different credential
            out["credential_conflict"] = {"with": [c.name for c in clash], "message": credential_conflict_message(p, clash)}
    return out


def config_view(brain: Brain, p: Pipeline) -> dict:
    return {
        "name": p.name, "paused": p.paused,
        "sources": [_jira_source(s) if s.type == "jira" else _folder_source(s) for s in p.sources],
        "include": list(p.include), "exclude": list(p.exclude), "target": p.target,
        "name_policy": name_policy_view(p), "destination": _destination(brain, p),
        "on_source_delete": p.on_source_delete,
        "guard": {"threshold_percent": p.guard_threshold_percent, "min_files": p.guard_min_files},
    }


def status_view(brain: Brain, p: Pipeline) -> dict:
    """The pipeline's status as the home shows it (same text and shape), plus its destination with the last commit and clone status."""
    from .state import StateError
    from .status import brain_status, only_missing_sources
    from .uiapi import _row_summary

    try:
        s = brain_status(brain, pipeline=p.name)["pipelines"][0]
    except StateError:
        return {"status": "error", "needs_you": False, "paused": p.paused, "summary": "state unreadable: restore from Needs you", "last_sync_ts": None, "last_run": None,
                "owned_files": 0, "pending_approval": False, "destination": describe_destination(brain, p),
                "last_changes": changes.row_view(brain, p.name)}
    last = s["last_run"]
    return {"status": s["status"], "needs_you": s["status"] == "error" and only_missing_sources(last) and not s.get("credential_conflict"),     # MD-136 AC3
            "paused": p.paused, "summary": _row_summary(s, p),
            "schedule": p.schedule, "last_sync": s.get("last_sync"), "next_sync": s.get("next_sync"),       # MD-121
            "last_sync_ts": s["last_success"] if s["last_success"] else (last["ts"] if last else None),
            "last_run": {"ts": last["ts"], "trigger": last["trigger"], "result": last["result"], "counts": last["counts"]} if last else None,
            "owned_files": s["owned_files"], "pending_approval": bool(s.get("pending_approval")), "destination": s["target"],
            "last_changes": changes.row_view(brain, p.name)}


SCHEMA = 1


def show_view(brain: Brain, name: str) -> dict:
    """What ``pipeline show NAME --json`` prints: the configuration (as in view mode) plus the status. Documented in the README."""
    p = load_config(brain).get(name)
    if p is None:
        raise NotFound(name)
    return {"schema": SCHEMA, **config_view(brain, p), "status": status_view(brain, p)}


def credential_text(label: str, c: dict | None) -> str:
    """One line fragment: ``API token: app vault "name" (set)``. Never a value."""
    if c is None:
        return f"{label}: not set"
    how = {"vault": "app vault", "env": "environment variable", "keychain": "OS keychain", "invalid": "not a valid reference"}.get(c["method"], c["method"])
    state = "set" if c["set"] is True else "not set" if c["set"] is False else "vault locked" if c.get("locked") else "not checked"
    return f"{label}: {how}{' ' + (chr(34) + c['name'] + chr(34) if c['method'] == 'vault' else c['name']) if c['name'] else ''} ({state})"
