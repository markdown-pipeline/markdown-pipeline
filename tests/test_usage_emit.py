"""MD-116 (FR-S2): emit() is the one way an event is created. Allowlisted, global properties added, safe across processes, never raises."""
from __future__ import annotations

import json
import threading

import pytest

import mdpipeline
from mdpipeline import usage
from mdpipeline.usage import UsageStore

CANARY = "ACME-secret-title /home/user/private.md https://acme.atlassian.net a@b.co ghp_abcdef123456"


class Recorder:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def __call__(self, batch):
        self.batches.append(batch)

    @property
    def events(self):
        return [e for b in self.batches for e in b]


@pytest.fixture
def sharing():
    UsageStore().set_consent("share")


def queued() -> list[dict]:
    return json.loads((usage.data_dir() / "usage.json").read_text())["queue"]


# --- emit ------------------------------------------------------------------------------------------------------------
def test_without_consent_emit_stores_nothing_at_all(app_data):
    assert usage.emit("app_opened", first_open=True) is False
    assert UsageStore().pending == 0


def test_an_event_that_is_not_on_the_allowlist_is_dropped_even_when_sharing(sharing):
    assert usage.emit("made_up_event", x=1) is False
    assert UsageStore().pending == 0


def test_emit_queues_the_cleaned_properties_with_the_global_ones(sharing):
    assert usage.emit("source_add_started", source_type="jira", entry_point="editor", title=CANARY) is True
    (entry,) = queued()
    props = entry["properties"]
    assert entry["event"] == "source_add_started" and entry["install_id"] == UsageStore().install_id
    assert props["source_type"] == "jira" and props["entry_point"] == "editor"
    assert props["app_version"] == mdpipeline.__version__ and props["deployment"] == "local" and props["session_id"]
    assert "title" not in props and CANARY not in json.dumps(entry)


def test_a_value_that_is_not_allowed_never_reaches_the_queue(sharing):
    usage.emit("sync_failed", error_class=CANARY, source_type="jira")
    (entry,) = queued()
    assert "error_class" not in entry["properties"] and CANARY not in json.dumps(entry)


def test_callers_cannot_override_the_global_properties(sharing):
    usage.emit("app_opened", first_open=True, deployment="hosted", session_id="mine", app_version="9.9.9", install_id="x")
    (entry,) = queued()
    assert entry["properties"]["deployment"] == "local" and entry["properties"]["session_id"] != "mine"
    assert entry["properties"]["app_version"] == mdpipeline.__version__ and entry["install_id"] != "x"


def test_the_session_id_is_random_stable_in_a_process_and_new_on_request(sharing):
    usage.emit("app_opened")
    usage.emit("app_opened")
    a, b = (e["properties"]["session_id"] for e in queued())
    assert a == b and len(a) >= 16
    usage.new_session()
    usage.emit("app_opened")
    assert queued()[-1]["properties"]["session_id"] != a


def test_emit_never_raises_even_when_the_data_directory_cannot_be_written(sharing, app_data, monkeypatch):
    monkeypatch.setattr(UsageStore, "_write", lambda self, data: (_ for _ in ()).throw(OSError("disk full")))
    assert usage.emit("app_opened") is False


def test_emit_never_raises_on_odd_input(sharing):
    assert usage.emit("source_add_started", source_type=object(), entry_point=[1, 2]) in (True, False)
    assert usage.emit(None) is False                         # type: ignore[arg-type]


def test_explicit_user_sends_go_out_even_with_sharing_off_and_carry_the_globals(app_data):
    transport = Recorder()
    assert UsageStore().sharing is False
    assert usage.send_chosen("source_requested", transport, source_id="notion", note=CANARY) is True
    (event,) = transport.events
    assert event["event"] == "source_requested" and event["chosen"] is True
    assert event["properties"]["source_id"] == "notion" and event["properties"]["deployment"] == "local"
    assert "note" not in event["properties"] and CANARY not in json.dumps(event)
    assert UsageStore().live_log()[-1]["event"] == "source_requested"


def test_an_explicit_send_of_an_unlisted_event_is_refused(app_data):
    transport = Recorder()
    assert usage.send_chosen("anything_else", transport, x=1) is False and transport.events == []


# --- storage safe across processes -----------------------------------------------------------------------------------
def test_events_recorded_at_the_same_time_from_many_places_are_all_kept(sharing):
    def work():
        store = UsageStore()
        for i in range(25):
            store.record("e", {"n": i})

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert UsageStore().pending == 200


def test_an_event_recorded_while_a_batch_is_being_sent_is_not_lost(sharing):
    store = UsageStore()
    store.record("first")
    seen = []

    def transport(batch):
        seen.append([e["event"] for e in batch])
        if len(seen) == 1:
            UsageStore().record("during")         # another process records while the network call is in flight

    store.flush(transport)
    assert seen == [["first"], ["during"]]        # it was kept, and sent by the same flush
    assert queued() == []


def test_a_flush_sends_at_most_a_bounded_number_of_batches_so_it_cannot_run_forever(sharing):
    store = UsageStore()
    for i in range(usage.BATCH_SIZE * (usage.MAX_BATCHES_PER_FLUSH + 3)):
        store.record("e", {"n": i})
    calls = []
    store.flush(lambda batch: calls.append(len(batch)))
    assert len(calls) == usage.MAX_BATCHES_PER_FLUSH
    assert UsageStore().pending == usage.BATCH_SIZE * 3          # the rest waits for the next flush


def test_a_producer_that_never_stops_cannot_keep_a_flush_running(sharing):
    store = UsageStore()
    store.record("first")
    calls = []

    def transport(batch):
        calls.append(batch)
        UsageStore().record("again")              # every send triggers a new event

    store.flush(transport)
    assert len(calls) == usage.MAX_BATCHES_PER_FLUSH


def test_every_queued_event_has_its_own_id(sharing):
    store = UsageStore()
    store.record("a")
    store.record("a")
    ids = [e["id"] for e in queued()]
    assert len(set(ids)) == 2 and all(len(i) >= 32 for i in ids)


def test_only_one_flush_runs_at_a_time_so_nothing_is_sent_twice(sharing):
    store = UsageStore()
    store.record("a")
    inner = Recorder()
    outer_calls = []

    def transport(batch):
        outer_calls.append(batch)
        assert UsageStore().flush(inner) == 0         # a second flusher (another process) skips while this one is running

    store.flush(transport)
    assert len(outer_calls) == 1 and inner.batches == []


def test_the_transport_receives_the_event_id_so_a_resend_is_deduplicated_by_the_analytics_host(sharing):
    store = UsageStore()
    store.record("a")
    sent_ids = []
    store.flush(lambda batch: sent_ids.extend(e["id"] for e in batch))
    seen = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(request, timeout):
        seen["body"] = json.loads(request.data)
        return Resp()

    usage.posthog_transport("phc_x", opener=opener)([{"event": "a", "properties": {}, "install_id": "i", "id": sent_ids[0]}])
    assert seen["body"]["batch"][0]["uuid"] == sent_ids[0]
