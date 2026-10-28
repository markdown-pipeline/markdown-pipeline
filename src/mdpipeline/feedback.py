"""Request a source, and one question at three moments (MD-123, FR-S9).

Both are things the person chooses to send: they go out only when they press Send, even with usage sharing off, and the words they
type are labelled "goes to the team" (see ``usage.send_chosen``). A prompt is shown once, can be dismissed, and never blocks. Which
prompt is due is read from what is already on this computer (the activity log and the config); nothing here reads the network.
What was shown or answered is kept in one small JSON file in the app data directory, never inside a brain.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from . import usage
from .agents import READ_TOOLS
from .brain import Brain
from .log import ActivityLog
from .usage_events import SOURCE_ID, EmailAddress
from .vault import data_dir

NO_AGENT_READ_DAYS = 3
SEND_FAILED = "We couldn't send this right now. Check your connection and try again."


def _opts(*pairs: tuple[str, str]) -> list[dict]:
    return [{"answer": a, "label": label} for a, label in pairs]


QUESTIONS = {
    "stopped_before_first_sync": {"text": "Last time, setup ended before any files were synced. What got in the way?", "comment_label": "Anything to add? (optional)",
                                  "options": _opts(("no_time", "I ran out of time"), ("sign_in_trouble", "I couldn't sign in or get a token"),
                                                   ("step_unclear", "A step wasn't clear"), ("source_missing", "The source I need isn't supported yet"),
                                                   ("privacy_worry", "I was worried about privacy"), ("just_looking", "I was only looking around"),
                                                   ("broke", "Something broke"), ("other", "Something else"))},
    "last_source_removed": {"text": "Why did you remove it?", "comment_label": "Anything to add? (optional)",
                            "options": _opts(("just_testing", "I was only testing"), ("wrong_source", "I picked the wrong source"),
                                             ("not_useful", "It wasn't useful"), ("privacy_worry", "I was worried about privacy"),
                                             ("broke", "Something broke"), ("other", "Something else"))},
    "first_sync_expectation": {"text": "Did you get what you expected?", "comment_label": "Anything to add? (optional)",
                               "options": _opts(("yes", "Yes"), ("partly", "Partly"), ("no", "No"))},
    "no_agent_read_3d": {"text": "No connected agent has read your files yet. Which fits best?", "comment_label": "Anything to add? (optional)",
                         "options": _opts(("no_agent_installed", "I don't use a coding agent"),
                                          ("reads_folder_directly", "My agent already reads the files from the folder"),
                                          ("dont_know_how", "I didn't know how to connect it"), ("tried_failed", "I tried and it didn't work"),
                                          ("no_time", "I haven't had time yet"), ("files_not_useful", "The files weren't what I needed"),
                                          ("other", "Something else"))},
}
ORDER = ("stopped_before_first_sync", "last_source_removed", "first_sync_expectation", "no_agent_read_3d")      # the most specific moment first


# --- what was shown or answered ------------------------------------------------------------------------------------
def _path():
    return data_dir() / "feedback.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"shown": data.get("shown") if isinstance(data.get("shown"), dict) else {},
            "handled": data.get("handled") if isinstance(data.get("handled"), dict) else {},
            "first_run_ended": data.get("first_run_ended") if isinstance(data.get("first_run_ended"), str) else ""}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def handled() -> dict[str, str]:
    """``{question_id: "answered" | "dismissed"}``."""
    return dict(_read()["handled"])


def _mark(question_id: str, how: str) -> None:
    data = _read()
    data["handled"][question_id] = how
    _save(data)


# --- which prompt is due ---------------------------------------------------------------------------------------------
def _when(event: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_sync(log: ActivityLog) -> datetime | None:
    """When the first sync that wrote files finished (the earliest run that created something and did not fail)."""
    times = [_when(e) for e in log.read(types={"run"}) if e.get("result") != "error" and (e.get("counts") or {}).get("created", 0) > 0]
    times = [t for t in times if t]
    return min(times) if times else None


def note_first_run_ended() -> None:
    """MD-168 M3: the first run was left before the first files (the page says so on close, with or without usage sharing). Only the launch
    it happened in is kept, so the question waits for the next app open. Nothing about the person is stored."""
    data = _read()
    data["first_run_ended"] = usage.current_session_id()
    _save(data)


def _stalled_before_now(log: ActivityLog) -> bool:
    ended = _read().get("first_run_ended")
    return bool(ended) and ended != usage.current_session_id() and _first_sync(log) is None


def _due(question_id: str, brain: Brain, now: datetime) -> bool:
    from .config import load_config

    log = ActivityLog(brain)
    if question_id == "stopped_before_first_sync":
        return _stalled_before_now(log)
    pipelines = load_config(brain).pipelines
    if question_id == "last_source_removed":
        return not pipelines and any(e.get("action") == "remove" for e in log.read(types={"event"}))
    if not pipelines:
        return False
    first = _first_sync(log)
    if first is None:
        return False
    if question_id == "first_sync_expectation":
        return True
    reads = [e for e in log.read(types={"mcp_call"}) if e.get("tool") in READ_TOOLS]
    return not reads and now - first >= timedelta(days=NO_AGENT_READ_DAYS)


def next_prompt(brain: Brain, now: datetime | None = None) -> dict | None:
    """The one question due now, or None. Asking marks it as shown: a prompt is never put in front of the person twice."""
    now = now or datetime.now(timezone.utc)
    data = _read()
    for question_id in ORDER:
        if question_id in data["shown"] or question_id in data["handled"] or not _due(question_id, brain, now):
            continue
        data["shown"][question_id] = now.isoformat()
        _save(data)
        return {"question_id": question_id, **QUESTIONS[question_id], "goes_to_the_team": True}
    return None


def dismiss(question_id: str) -> None:
    """Not now, not ever: nothing is sent."""
    if question_id in QUESTIONS:
        _mark(question_id, "dismissed")


# --- sending (only from a Send button) ---------------------------------------------------------------------------------
def answer(question_id: str, answer_id: str, comment: str, transport) -> dict:
    """Send the person's answer and optional comment to the team. ValueError for a question or answer that does not exist."""
    question = QUESTIONS.get(question_id)
    if question is None or answer_id not in {o["answer"] for o in question["options"]}:
        raise ValueError("unknown question or answer")
    props = {"question_id": question_id, "answer": answer_id}
    if (comment or "").strip():
        props["comment"] = comment
    if not usage.send_chosen("feedback_answered", transport, **props):
        return {"ok": False, "error": SEND_FAILED}
    _mark(question_id, "answered")
    return {"ok": True}


def request_source(source_id: str, other_source: str, email: str, transport) -> dict:
    """Send one request for a source. The typed name counts only for "other"; a bad email is refused, not silently dropped."""
    if source_id not in SOURCE_ID.values:
        raise ValueError("unknown source")
    props: dict = {"source_id": source_id}
    if source_id == "other" and (other_source or "").strip():
        props["other_source"] = other_source
    if (email or "").strip():
        if EmailAddress().check(email) is None:
            return {"ok": False, "error": "That doesn't look like an email address. Leave it empty if you don't want to be emailed."}
        props["email"] = email
    if not usage.send_chosen("source_requested", transport, **props):
        return {"ok": False, "error": SEND_FAILED}
    return {"ok": True}
