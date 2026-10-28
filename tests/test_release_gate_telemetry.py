"""MD-126 AC1-3 (release gate): no content in telemetry, "Don't share" = zero analytics requests, and a down or slow analytics host
changes nothing the person sees. A whole realistic journey runs against fakes; the analytics HTTP layer is captured. Run alone:

    uv run pytest tests/test_release_gate_telemetry.py
"""
import json
import socket
import threading
import time

import pytest

from fake_jira import FakeJira
from mdpipeline import jira_api, notices, scheduler, usage
from mdpipeline.config import Pipeline, Source, load_config
from mdpipeline.engine import sync_all, sync_pipeline
from mdpipeline.service import create_pipeline, set_paused, set_schedule, update_pipeline
from mdpipeline.usage import UsageStore, posthog_transport
from mdpipeline.usage_events import EVENTS, sanitize
from test_jira_sync import EMAIL, SITE, TOKEN, day, jira_source

HOUR = 3600
GLOBALS = {"app_version", "deployment", "session_id", "is_internal"}

# What must never leave the machine: the names, titles, queries, hosts, addresses, secrets and error texts of the test corpus.
FORBIDDEN = ["Zephyrcorp", "zephyr-merger", "projectxenon", "xenonlabel", "xenonpipe", "XENONSECRET", "atlassian.net", "acme",
             EMAIL, TOKEN, "scripted failure", "Mia Owner", "/tmp/", "pytest-of", "Confidential acquisition"]


class Wire:
    """The analytics HTTP layer: every request the real PostHog transport would make lands here instead of the network."""

    def __init__(self, behaviour=None) -> None:
        self.requests: list[dict] = []
        self.behaviour = behaviour

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        self.requests.append({"url": request.full_url, "body": body})
        if self.behaviour:
            self.behaviour()

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        return Response()

    @property
    def sent_text(self) -> str:
        return json.dumps(self.requests)


@pytest.fixture(autouse=True)
def no_other_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(notices, "os_notify", lambda title, body: True)


@pytest.fixture
def wire(monkeypatch):
    w = Wire()
    transport = posthog_transport("phc_test_key", opener=w)
    monkeypatch.setattr(usage, "get_transport", lambda: transport)
    usage.cancel_pending_flush()
    yield w
    usage.cancel_pending_flush()


@pytest.fixture
def fake(monkeypatch):
    f = FakeJira(site="acme", email=EMAIL, token=TOKEN, display_name="Mia Owner")
    f.sleeps = []
    jira_api.set_transport(f, sleep=f.sleeps.append)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_TOKEN", TOKEN)
    yield f
    jira_api.set_transport(None)


@pytest.fixture
def world(brain, add_pipeline, make_source, fake):
    docs = make_source("projectxenon", {"zephyr-merger-memo.md": "# Zephyrcorp merger\nterms", "notes.md": "# Notes\nbody"})
    fake.add_issue("XENONSECRET", "Confidential acquisition of Zephyrcorp", created=day(2026, 10, 1))
    fake.add_issue("XENONSECRET", "Second issue", created=day(2026, 10, 2))
    gone = make_source("projectxenon-gone", {"zephyr-old.md": "# Zephyrcorp old"})
    blocked = make_source("projectxenon-blocked", {f"zephyr-{i}.md": f"# Zephyrcorp {i}" for i in range(10)})
    create_pipeline(brain, Pipeline.new("xenonpipe", [Source(str(docs), "xenonlabel")]))
    add_pipeline(Pipeline.new("xenongone", [str(gone)]))
    add_pipeline(Pipeline.new("xenonblocked", [str(blocked)]))
    add_pipeline(Pipeline("jirapipe", [jira_source("project = XENONSECRET")]))
    return type("W", (), {"brain": brain, "docs": docs, "fake": fake, "gone": gone, "blocked": blocked})


def journey(w) -> None:
    """A first run plus three syncs, an edit, schedule changes, a failing scheduled run with its notices, and pause/resume."""
    brain = w.brain
    sync_all(brain, trigger="ui")                                   # the first run
    (w.docs / "zephyr-merger-memo.md").write_text("# Zephyrcorp merger\nnew terms")
    sync_all(brain, trigger="cli")
    w.fake.add_issue("XENONSECRET", "Another confidential thing", created=day(2026, 10, 3))
    sync_all(brain, trigger="mcp")
    cfg = load_config(brain)
    p = cfg.get("xenonpipe")
    p.include = ["**/*.md", "**/*.txt"]
    update_pipeline(brain, "xenonpipe", p)
    set_schedule(brain, "xenonpipe", "6h", trigger="ui")
    set_schedule(brain, "xenonpipe", "1h", trigger="cli")
    import shutil

    shutil.rmtree(w.gone)                                            # a source goes missing: the source_missing notice
    for f in list(w.blocked.glob("*.md"))[:8]:                       # a big change: the guard_blocked notice
        f.unlink()
    w.fake.fail("GET", "/search/jql", status=503, times=99)
    for hour in (2, 3, 4):
        scheduler.tick(brain, now=time.time() + hour * HOUR)         # failing scheduled runs: errors, notices
    set_paused(brain, "xenonpipe", True, trigger="ui")
    set_paused(brain, "xenonpipe", False, trigger="ui")


def share() -> None:
    UsageStore().set_consent("share")


# --- AC1 --------------------------------------------------------------------------------------------------------------
def test_ac1_every_event_of_a_whole_journey_passes_the_allowlist_and_no_content_appears_anywhere(world, wire):
    share()
    journey(world)
    UsageStore().flush(usage.get_transport())
    events = [e for r in wire.requests for e in r["body"].get("batch", [])]
    assert len(events) >= 8, "the journey should have produced a realistic number of events"
    names = {e["event"] for e in events}
    assert {"sync_completed", "sync_failed", "schedule_changed", "notice_shown"} <= names
    kinds = {e["properties"]["kind"] for e in events if e["event"] == "notice_shown"}
    assert {"sync_failed", "source_missing", "guard_blocked"} <= kinds, kinds        # every notice kind is inside the content check
    # Any future notice kind must pass this same gate: add it to the journey above.
    for e in events:
        assert e["event"] in EVENTS, e
        props = {k: v for k, v in e["properties"].items() if k not in GLOBALS and not k.startswith("$") and k != "distinct_id"}
        assert sanitize(e["event"], props) == props, e            # nothing the allowlist would have dropped
        assert set(e["properties"]) - GLOBALS - set(EVENTS[e["event"]].props) <= {"$lib", "distinct_id", "install_id", "$process_person_profile"}, e
    for needle in FORBIDDEN:
        assert needle not in wire.sent_text, f"{needle!r} left the machine"
    local = (usage.data_dir() / "usage.json").read_text(encoding="utf-8")
    for needle in FORBIDDEN:
        assert needle not in local, f"{needle!r} is in the usage file on disk"


# --- AC2 --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("choice", ["decline", "unanswered"])
def test_ac2_without_consent_a_whole_journey_makes_zero_requests_to_the_analytics_host(world, wire, choice):
    UsageStore().set_consent(choice)
    journey(world)
    assert UsageStore().pending == 0
    UsageStore().flush(usage.get_transport())
    assert usage.schedule_flush() is False
    assert usage.wait_for_flush(5)
    assert wire.requests == []


def test_ac2_sharing_turned_off_after_it_was_on_drops_what_was_waiting_and_sends_nothing(world, wire):
    share()
    journey(world)
    UsageStore().set_consent("decline")
    UsageStore().flush(usage.get_transport())
    assert wire.requests == [] and UsageStore().pending == 0


# --- AC3 --------------------------------------------------------------------------------------------------------------
def test_ac3_a_hung_analytics_host_does_not_slow_the_sync_or_show_an_error(world, wire, monkeypatch, capsys):
    share()
    release = threading.Event()
    wire.behaviour = lambda: release.wait(10)
    started = time.perf_counter()
    results = sync_all(world.brain, trigger="cli")
    elapsed = time.perf_counter() - started
    release.set()
    assert {r.result for r in results} == {"ok"}
    assert elapsed < 3.0                                    # the send happens later, in the background
    assert capsys.readouterr().err == ""


def test_ac3_an_analytics_host_that_is_down_changes_nothing_the_person_sees(world, monkeypatch, capsys):
    share()

    def down(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(usage, "get_transport", lambda: posthog_transport("phc_test_key", opener=down))
    results = sync_all(world.brain, trigger="cli")
    UsageStore().flush(usage.get_transport())               # the failed send is quiet and the events stay queued for later
    assert {r.result for r in results} == {"ok"} and UsageStore().pending > 0
    out = capsys.readouterr()
    assert out.err == "" and "refused" not in out.out


def test_ac3_sync_time_is_unchanged_within_noise_with_a_slow_host_versus_no_transport(world, wire, monkeypatch):
    share()

    def timed() -> float:
        started = time.perf_counter()
        for _ in range(3):
            sync_pipeline(world.brain, "xenonpipe", trigger="cli")
        return time.perf_counter() - started

    monkeypatch.setattr(usage, "get_transport", lambda: None)
    baseline = timed()
    slow = threading.Event()
    wire.behaviour = lambda: slow.wait(5)
    monkeypatch.setattr(usage, "get_transport", lambda: posthog_transport("phc_test_key", opener=wire))
    with_slow_host = timed()
    slow.set()
    assert with_slow_host < baseline + 1.0
