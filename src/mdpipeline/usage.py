"""Anonymous usage data (MD-115 / FR-S1): the consent choice, the random install id, and what is stored locally.

Everything lives in ``<data dir>/usage.json``. Nothing here touches the network: a transport is passed in by the caller, and
without consent no event is ever queued or sent.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__, filelock
from .autostart import detached_kwargs
from .usage_events import sanitize
from .vault import data_dir

CONSENT_VALUES = ("share", "decline", "unanswered")     # "unanswered" = continued without choosing; treated as "don't share"
BATCH_SIZE = 20          # events per send
FLUSH_DELAY = 3.0              # seconds events wait so a burst goes out as one batch (long-lived processes: UI server, MCP server)
FLUSH_MIN_INTERVAL = 60.0      # a short CLI run starts a detached sender at most this often
MAX_BATCHES_PER_FLUSH = 10     # one flush sends at most this many batches; the rest waits for the next one
QUEUE_LIMIT = 500        # an install that stays offline keeps only the newest
LOG_LIMIT = 50           # the live log shows the last 50 events sent

CONSENT_OUTCOMES = ("share", "decline", "no_answer")

# The wording shown in Settings → Privacy & usage, the first-run drawer and `mdpipeline usage` (approved design, MP-51).
WHAT_WE_SEND = ("Setup steps you complete", "Sync results: counts, timings and kinds of error", "Agent reads per day",
                "App version and operating system", "A random install ID")
NEVER_SENT = ("Your content or titles", "File or folder names, paths", "Jira site, projects, JQL, queries or filter values",
              "Emails (except one you give us to be told about a new tool), tokens, error text")
ALWAYS_SENT = ("Every event also carries your random install ID, the time, the app version, a random session ID (new at each launch), "
               "deployment = local, and $process_person_profile = false (it tells the analytics service not to build a person profile). "
               "The install ID is the only identifier.")
SENT_ON_PURPOSE = ("Sent only when you press Send, even with sharing off: a source request (\"Use another tool? Tell us which\") and your "
                   "answers to a one-question prompt. Each is labelled \"goes to the team\". It includes the words you type there and, if you give one, your email.")
POSTHOG_HOST = "https://us.i.posthog.com"       # PostHog US cloud (project "Markdown Pipeline")
# The project key of "Markdown Pipeline" (provided by the owner, 2026-10-08). A ``phc_`` key can only ingest events: PostHog designs it to be
# public in client apps, so shipping it is safe. It is NOT a personal API key (``phx_``), which must never appear in the repo.
# ``MDPIPELINE_POSTHOG_KEY`` overrides it (set it empty to switch analytics off, e.g. for development).
POSTHOG_PROJECT_KEY = "phc_uXEq7eYy8y4mFUtgDrwPxUy9Upb4d7aNjTpwCeJ8QiTn"
POSTHOG_ALLOWED_HOST = "us.i.posthog.com"
POSTHOG_TIMEOUT = 5
COUNTER_DISTINCT_ID = "anonymous-counter"

Transport = Callable[[list[dict]], None]     # sends one batch, raises on failure


def posthog_transport(project_key: str, host: str = POSTHOG_HOST, opener=urllib.request.urlopen, *, allowed_host: str = POSTHOG_ALLOWED_HOST) -> Transport:
    """A transport that posts a batch to PostHog's ``/batch/`` endpoint. Anonymous: no person profile is created.

    https only, and only the analytics host: anything else is refused before a request is made.
    """

    def send(batch: list[dict]) -> None:
        parts = urllib.parse.urlsplit(host)
        if parts.scheme != "https" or parts.hostname != allowed_host:
            raise ValueError(f"refusing to send usage data to {parts.scheme}://{parts.hostname}: only https://{allowed_host} is allowed")
        items = []
        for event in batch:
            install_id = event.get("install_id")
            properties = {**event.get("properties", {}), "$process_person_profile": False}     # the only addition: disclosed in ALWAYS_SENT
            items.append({"event": event["event"], "distinct_id": install_id or COUNTER_DISTINCT_ID, "properties": properties,
                          **({"timestamp": event["at"]} if event.get("at") else {}),
                          **({"uuid": event["id"]} if event.get("id") else {})})          # the analytics host drops a resent duplicate by uuid
        body = json.dumps({"api_key": project_key, "batch": items}).encode("utf-8")
        request = urllib.request.Request(host + "/batch/", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with opener(request, timeout=POSTHOG_TIMEOUT) as response:
            if not 200 <= response.status < 300:
                raise OSError(f"analytics host answered {response.status}")

    return send


def get_transport() -> Transport | None:
    """What the app (CLI, UI, MCP) sends with. One place, so tests can replace it and no test can reach the analytics host."""
    return default_transport()


def default_transport(project_key: str | None = None) -> Transport | None:
    """The real transport, or None when no project key is configured (the owner provides it): then nothing is ever sent."""
    key = os.environ.get("MDPIPELINE_POSTHOG_KEY", POSTHOG_PROJECT_KEY) if project_key is None else project_key
    return posthog_transport(key) if key else None


_thread_lock = threading.RLock()
_held = threading.local()


@contextlib.contextmanager
def _file_lock(path: Path, *, blocking: bool = True):
    """An advisory lock on ``path`` shared by every process of this user (UI server, CLI runs, MCP server). Yields whether it was taken."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if not filelock.try_lock(handle.fileno(), blocking=blocking):
            yield False
            return
        try:
            yield True
        finally:
            filelock.unlock(handle.fileno())
    finally:
        handle.close()


class UsageStore:
    """The local usage-data state. Every instance reads the file fresh, so a change is effective immediately.

    Every change is a read-modify-write under one lock that all processes share, so events recorded by the UI server, a CLI run and the
    MCP server at the same moment are all kept. The network is never touched while that lock is held.
    """

    def __init__(self) -> None:
        self._path: Path = data_dir() / "usage.json"

    # -- storage -------------------------------------------------------------------------------------------------
    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    @contextlib.contextmanager
    def _locked(self):
        """Hold the lock for one read-modify-write (re-entrant within a thread)."""
        if getattr(_held, "depth", 0):
            _held.depth += 1
            try:
                yield
            finally:
                _held.depth -= 1
            return
        with _thread_lock, _file_lock(self._path.with_name("usage.lock")):
            _held.depth = 1
            try:
                yield
            finally:
                _held.depth = 0

    @contextlib.contextmanager
    def _update(self):
        """``with store._update() as data:`` change ``data``; it is saved when the block ends without an error."""
        with self._locked():
            data = self._read()
            yield data
            self._write(data)

    # -- consent -------------------------------------------------------------------------------------------------
    @property
    def consent(self) -> str:
        value = self._read().get("consent")
        return value if value in CONSENT_VALUES else "unanswered"

    @property
    def sharing(self) -> bool:
        """True only for an explicit "share"."""
        return self.consent == "share"

    def set_consent(self, choice: str) -> None:
        if choice not in CONSENT_VALUES:
            raise ValueError(f"consent must be one of {CONSENT_VALUES}")
        with self._update() as data:
            data["consent"] = choice
            if choice != "share":
                data["queue"] = []          # effective immediately: whatever waited to be sent is dropped

    # -- events --------------------------------------------------------------------------------------------------
    @property
    def pending(self) -> int:
        return len(self._queue(self._read()))

    @staticmethod
    def _queue(data: dict) -> list[dict]:
        queue = data.get("queue")
        return queue if isinstance(queue, list) else []

    def record(self, event: str, properties: dict | None = None, at: str | None = None) -> bool:
        """Queue an event. Without an explicit "share" nothing is stored at all. Returns whether it was queued.

        ``at`` dates the event (default: now); a day's agent-read count is dated to that day.
        """
        with self._update() as data:
            if data.get("consent") != "share":
                return False
            queue = self._queue(data)
            queue.append(self._entry(data, event, properties, chosen=False, at=at))
            data["queue"] = queue[-QUEUE_LIMIT:]
            return True

    def flush(self, transport: Transport | None) -> int:
        """Send the queued events in batches. Never raises; a failed batch stays queued and ends the attempt. Returns how many were sent.

        Only one flush runs at a time across all processes (a second one returns 0), and events recorded during a send are kept.
        """
        if transport is None:
            return 0
        sent = 0
        with _file_lock(self._path.with_name("flush.lock"), blocking=False) as got:
            if not got:
                return 0
            for _ in range(MAX_BATCHES_PER_FLUSH):          # bounded: a producer that never stops cannot keep one flush running forever
                with self._locked():
                    data = self._read()
                    if data.get("consent") != "share":
                        return sent
                    batch = self._queue(data)[:BATCH_SIZE]
                if not batch:
                    return sent
                try:
                    transport(batch)         # outside the lock: a slow network never blocks anyone recording an event
                except Exception:            # noqa: BLE001 - analytics must never fail, slow or surface into the caller
                    return sent
                done = {e.get("id") for e in batch}
                with self._update() as data:
                    data["queue"] = [e for e in self._queue(data) if e.get("id") not in done]
                    self._append_log(data, batch)
                sent += len(batch)
            return sent

    def send_user_chosen(self, event: str, properties: dict | None, transport: Transport) -> bool:
        """Send something the user pressed Send on (MD-123). Goes out even with sharing off; shown in the log as chosen by the user."""
        entry = self._entry(self._read(), event, properties, chosen=True)
        try:
            transport([entry])
        except Exception:               # noqa: BLE001
            return False
        with self._update() as data:
            self._append_log(data, [entry])
        return True

    # -- deciding, notices, view ---------------------------------------------------------------------------------
    def decide(self, choice: str, transport: Transport | None = None) -> None:
        """The user's answer to the consent card / prompt: ``share``, ``decline`` or ``no_answer`` (carried on without choosing).

        Only an install's FIRST decision is counted (when the counter is on), so the consent rate stays shares / everyone who saw the card.
        """
        if choice not in CONSENT_OUTCOMES:
            raise ValueError(f"choice must be one of {CONSENT_OUTCOMES}")
        if choice != "no_answer":
            self.set_consent(choice)
        if choice == "share":
            emit("consent_decided", choice="share")        # only a share can be an event: a decline sends nothing
            report_install_once()                          # MD-117: the install funnel's first event, at first launch after consent
        if self._read().get("outcome_counted"):
            return
        if self.report_consent_outcome(choice, transport):
            with self._update() as data:
                data["outcome_counted"] = True

    def notice_seen(self, name: str) -> bool:
        seen = self._read().get("notices")
        return isinstance(seen, list) and name in seen

    def mark_notice_seen(self, name: str) -> None:
        with self._update() as data:
            seen = data.get("notices") if isinstance(data.get("notices"), list) else []
            if name not in seen:
                data["notices"] = [*seen, name]

    def view(self) -> dict:
        """Everything the UI and the CLI show: the choice, what is and is never sent, the install id and the live log (newest first)."""
        return {"consent": self.consent, "sharing": self.sharing, "install_id": self.install_id,
                "what_we_send": list(WHAT_WE_SEND), "never_sent": list(NEVER_SENT), "sent_on_purpose": SENT_ON_PURPOSE,
                "always_sent": ALWAYS_SENT,
                "log": list(reversed(self.live_log())), "log_limit": LOG_LIMIT}

    # -- consent counter (AC8) -----------------------------------------------------------------------------------
    @property
    def counter_enabled(self) -> bool:
        """Off by default until the owner's legal read; when on, only an identifier-free outcome count is sent."""
        return self._read().get("counter_enabled") is True

    def set_counter_enabled(self, enabled: bool) -> None:
        with self._update() as data:
            data["counter_enabled"] = bool(enabled)

    def report_consent_outcome(self, outcome: str, transport: Transport | None) -> bool:
        """Count one share / decline / no_answer decision. No install id, no timestamp, not in the live log."""
        if outcome not in CONSENT_OUTCOMES:
            raise ValueError(f"outcome must be one of {CONSENT_OUTCOMES}")
        if not self.counter_enabled or transport is None:
            return False
        try:
            transport([{"event": "consent_outcome", "properties": {"outcome": outcome}}])
        except Exception:               # noqa: BLE001
            return False
        return True

    def live_log(self) -> list[dict]:
        """The last events actually sent (name + properties), oldest first."""
        log = self._read().get("log")
        return log if isinstance(log, list) else []

    def _entry(self, data: dict, event: str, properties: dict | None, chosen: bool, at: str | None = None) -> dict:
        install_id = data.get("install_id")
        if not isinstance(install_id, str) or not install_id:
            install_id = data["install_id"] = str(uuid.uuid4())
        return {"id": str(uuid.uuid4()), "event": event, "properties": dict(properties or {}), "install_id": install_id, "chosen": chosen,
                "at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    @staticmethod
    def _append_log(data: dict, sent: list[dict]) -> None:
        log = data.get("log") if isinstance(data.get("log"), list) else []
        data["log"] = (log + sent)[-LOG_LIMIT:]

    # -- the time-to-value clock (kept locally even without consent, so a later opt-in still has the right time) --------
    @staticmethod
    def _utc(now: datetime | None) -> datetime:
        return now if now is not None else datetime.now(timezone.utc)

    def _stamp_once(self, key: str, now: datetime | None) -> bool:
        with self._update() as data:
            if isinstance(data.get(key), (int, float)):
                return False
            data[key] = self._utc(now).timestamp()
            return True

    def _ms_since(self, key: str, now: datetime | None) -> int | None:
        started = self._read().get(key)
        if not isinstance(started, (int, float)):
            return None
        return max(0, int((self._utc(now).timestamp() - started) * 1000))

    def touch_first_open(self, now: datetime | None = None) -> bool:
        """Remember the first time the app was opened. True only for the call that remembered it."""
        return self._stamp_once("first_open_ts", now)

    def ms_since_first_open(self, now: datetime | None = None) -> int | None:
        return self._ms_since("first_open_ts", now)

    def note_first_sync(self, now: datetime | None = None) -> bool:
        """Remember the first completed sync. True only for the call that remembered it."""
        return self._stamp_once("first_sync_ts", now)

    def ms_since_first_sync(self, now: datetime | None = None) -> int | None:
        return self._ms_since("first_sync_ts", now)

    # -- agent reads: one counter per UTC day ----------------------------------------------------------------------------
    def count_agent_read(self, day: str) -> None:
        with self._update() as data:
            reads = data.get("agent_reads") if isinstance(data.get("agent_reads"), dict) else {}
            reads[day] = int(reads.get(day, 0)) + 1
            data["agent_reads"] = reads

    def take_past_agent_reads(self, today: str) -> dict[str, int]:
        """Remove and return the counts of the days before ``today`` (ISO dates compare as text)."""
        with self._update() as data:
            reads = data.get("agent_reads") if isinstance(data.get("agent_reads"), dict) else {}
            past = {day: int(n) for day, n in reads.items() if day < today}
            if past:
                data["agent_reads"] = {day: n for day, n in reads.items() if day >= today}
            return past

    def swap_version(self, current: str) -> str | None:
        """Remember the running version. Returns the previous one when it differs (the app was updated), else None."""
        with self._update() as data:
            previous = data.get("last_version")
            data["last_version"] = current
            return previous if isinstance(previous, str) and previous != current else None

    def claim_daily(self, name: str, day: str) -> bool:
        """True for the first caller of ``name`` on ``day`` (an ISO date) only: a once-a-day event such as the linger notice per screen."""
        with self._update() as data:
            days = data.get("daily") if isinstance(data.get("daily"), dict) else {}
            if days.get(name) == day:
                return False
            data["daily"] = {**days, name: day}
            return True

    def claim_once(self, name: str) -> bool:
        """True for the first caller only, ever (e.g. the one-time ``first_agent_read`` event)."""
        with self._update() as data:
            done = data.get("once") if isinstance(data.get("once"), list) else []
            if name in done:
                return False
            data["once"] = [*done, name]
            return True

    # -- install id ----------------------------------------------------------------------------------------------
    @property
    def install_id(self) -> str:
        value = self._read().get("install_id")
        if isinstance(value, str) and value:
            return value
        with self._update() as data:                  # first use: create it (a second process may have created it meanwhile)
            if not isinstance(data.get("install_id"), str) or not data["install_id"]:
                data["install_id"] = str(uuid.uuid4())
            return data["install_id"]

    def reset_install_id(self) -> str:
        with self._update() as data:
            data["install_id"] = str(uuid.uuid4())
            return data["install_id"]


# --- the one way an event is created -----------------------------------------------------------------------------------
_session_id = uuid.uuid4().hex


def current_session_id() -> str:
    """The random id of this launch (also what tells "the next app open" apart from the one that is running)."""
    return _session_id


def new_session() -> None:
    """A new random session id (the app calls this once per launch; tests call it to simulate a second launch)."""
    global _session_id
    _session_id = uuid.uuid4().hex


INTERNAL_ENV = "MDPIPELINE_INTERNAL"
_YES, _NO = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def internal_flag() -> bool:
    """MD-154: is this install ours (the owner's, an agent's, a test)? ``MDPIPELINE_INTERNAL=1`` marks it and the mark is kept in the usage file,
    so it survives restarts and upgrades without the variable; ``MDPIPELINE_INTERNAL=0`` takes it off. Anything else in the variable is ignored.
    A plain true/false: it carries nothing about the person. It changes nothing about consent (events still need the person's yes)."""
    try:
        raw = os.environ.get(INTERNAL_ENV, "").strip().lower()
        store = UsageStore()
        kept = bool(store._read().get("internal"))
        wanted = True if raw in _YES else False if raw in _NO else kept
        if wanted != kept:
            with store._update() as data:
                data["internal"] = wanted
        return wanted
    except Exception:                   # noqa: BLE001 - the mark must never break an event or a command
        return False


def _globals() -> dict:
    """Added to every emitted event, and visible in the live log: callers cannot set or override these."""
    return {"app_version": __version__, "deployment": "local", "session_id": _session_id, "is_internal": internal_flag()}


def emit(name: str, /, **properties) -> bool:
    """Record a usage event: only an allowlisted event, only allowlisted properties and values, only with consent. Never raises.

    Returns whether the event was queued. Sending is separate (batched, in the background) and never delays the caller.
    """
    try:
        clean = sanitize(name, properties)
        if clean is None:
            return False
        store = UsageStore()
        if not store.sharing:
            return False
        queued = store.record(name, {**clean, **_globals()})
        if queued:
            schedule_flush()
        return queued
    except Exception:                   # noqa: BLE001 - analytics must never fail, slow or surface into what the user is doing
        return False


def report_install_once() -> bool:
    """``install_completed`` (OS and install method, both fixed names), once per install, only while sharing. Never raises."""
    try:
        store = UsageStore()
        if not store.sharing or store._read().get("install_reported"):
            return False
        from .install_info import install_method, os_name

        if not emit("install_completed", os=os_name(), install_method=install_method()):
            return False
        with store._update() as data:
            data["install_reported"] = True
        return True
    except Exception:                   # noqa: BLE001
        return False


def send_chosen(name: str, transport: Transport | None, /, **properties) -> bool:
    """Send an event the user pressed Send on (a source request, a feedback answer): out even with sharing off, labelled in the log."""
    try:
        clean = sanitize(name, properties, explicit=True)
        if clean is None or transport is None:
            return False
        return UsageStore().send_user_chosen(name, {**clean, **_globals()}, transport)
    except Exception:                   # noqa: BLE001
        return False


# --- sending: never in the caller's way ---------------------------------------------------------------------------------
_timer_lock = threading.Lock()
_pending_timer: threading.Timer | None = None
_flush_idle = threading.Event()
_flush_idle.set()


def schedule_flush() -> bool:
    """For long-lived processes (UI server, MCP server): send what is queued from a background daemon thread after a short delay.

    Events that arrive within the delay go out as one batch. Returns whether a send was scheduled (False: nothing to do, or one is pending).
    """
    global _pending_timer
    try:
        if get_transport() is None or not UsageStore().sharing:
            return False
        with _timer_lock:
            if _pending_timer is not None:
                return False
            timer = threading.Timer(FLUSH_DELAY, _run_flush)
            timer.daemon = True                 # never keeps a process alive
            _pending_timer = timer
            _flush_idle.clear()
            timer.start()
            return True
    except Exception:                           # noqa: BLE001
        return False


def _run_flush() -> None:
    global _pending_timer
    sent = 0
    try:
        sent = UsageStore().flush(get_transport())
    except Exception:                           # noqa: BLE001
        pass
    with _timer_lock:
        _pending_timer = None
        _flush_idle.set()
    if sent:                                    # more arrived while sending: go again (a failing network is NOT retried in a loop)
        try:
            if UsageStore().pending:
                schedule_flush()
        except Exception:                       # noqa: BLE001
            pass


def wait_for_flush(timeout: float) -> bool:
    """Wait until no background send is pending or running (used at shutdown and by tests)."""
    return _flush_idle.wait(timeout)


def cancel_pending_flush() -> None:
    global _pending_timer
    with _timer_lock:
        if _pending_timer is not None:
            _pending_timer.cancel()
            _pending_timer = None
        _flush_idle.set()


def flush_detached() -> bool:
    """For a short CLI run: hand the queue to a detached helper process (``mdpipeline usage flush``) and return at once.

    The command never waits for the network. Throttled to one helper per FLUSH_MIN_INTERVAL, so a busy cron job does not spawn one per run.
    """
    try:
        if get_transport() is None:
            return False
        with UsageStore()._update() as data:
            if data.get("consent") != "share" or not UsageStore._queue(data):
                return False
            now = time.time()
            if now - float(data.get("last_flush_attempt") or 0) < FLUSH_MIN_INTERVAL:
                return False
            data["last_flush_attempt"] = now
        subprocess.Popen([sys.executable, "-m", "mdpipeline", "usage", "flush"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **detached_kwargs())
        return True
    except Exception:                           # noqa: BLE001 - a missing helper only means the events go out on a later run
        return False


def note_agent_read(now: datetime | None = None) -> bool:
    """One MCP read by an agent: counted for today (a count, nothing else). Without consent nothing is stored. Never raises.

    The previous days' counts are queued as one ``agent_read`` event per day (dated to that day); the very first read ever queues
    ``first_agent_read`` with the time since the first sync.
    """
    try:
        store = UsageStore()
        if not store.sharing:
            return False
        now = now if now is not None else datetime.now(timezone.utc)
        today = now.date().isoformat()
        queued = False
        for day, count in sorted(store.take_past_agent_reads(today).items()):
            queued = store.record("agent_read", {"reads_count": count, **_globals()}, at=f"{day}T23:59:00Z") or queued
        store.count_agent_read(today)
        if store.claim_once("first_agent_read"):
            ms = store.ms_since_first_sync(now)
            clean = sanitize("first_agent_read", {"ms_since_first_sync": ms}) or {}
            queued = store.record("first_agent_read", {**clean, **_globals()}) or queued
        if queued:
            schedule_flush()
        return True
    except Exception:                   # noqa: BLE001
        return False
