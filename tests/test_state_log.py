"""Pipeline state (ownership records) and the local activity log (MD-19 storage layer)."""
import json

import pytest

from mdpipeline.brain import init_brain
from mdpipeline.log import ActivityLog
from mdpipeline.state import FileRecord, State, StateError


@pytest.fixture
def brain(brain_dir):
    return init_brain(brain_dir)


def test_state_of_a_new_brain_is_empty(brain):
    assert State.load(brain).pipelines == {}


def test_state_round_trip(brain):
    st = State.load(brain)
    ps = st.pipeline("p")
    ps.files["raw/p/a.md"] = FileRecord("docs", "a.md", "sha256:s", "sha256:w")
    ps.files["raw/p/b.md"] = FileRecord("docs", "b.md", "sha256:s2", None, "sha256:pend")
    st.save()
    again = State.load(brain)
    assert again.pipeline("p").files == ps.files


def test_state_save_is_atomic_and_leaves_no_temp(brain):
    st = State.load(brain)
    st.pipeline("p")
    st.save()
    assert sorted(x.name for x in brain.config_dir.iterdir() if x.suffix == ".tmp") == []


def test_corrupt_state_is_an_error_not_an_empty_state(brain):
    (brain.config_dir / "state.json").write_text("{not json")
    with pytest.raises(StateError):
        State.load(brain)


def test_drop_pipeline_forgets_ownership(brain):
    st = State.load(brain)
    st.pipeline("p").files["raw/a.md"] = FileRecord("l", "a.md", "h", "h")
    st.drop_pipeline("p")
    assert "p" not in st.pipelines


def test_log_append_and_read_in_order(brain):
    log = ActivityLog(brain)
    log.append({"type": "run", "pipeline": "a", "result": "ok"})
    log.append({"type": "run", "pipeline": "b", "result": "error"})
    events = log.read()
    assert [e["pipeline"] for e in events] == ["a", "b"]
    assert all("ts" in e for e in events)


def test_log_filter_by_pipeline_and_limit_returns_most_recent(brain):
    log = ActivityLog(brain)
    for i in range(5):
        log.append({"type": "run", "pipeline": "a", "n": i})
    log.append({"type": "run", "pipeline": "b", "n": 99})
    assert [e["n"] for e in log.read(pipeline="a", limit=2)] == [3, 4]
    assert [e["n"] for e in log.read(pipeline="b")] == [99]


def test_log_skips_corrupt_lines(brain):
    log = ActivityLog(brain)
    log.append({"type": "run", "pipeline": "a"})
    with open(log.path, "a") as f:
        f.write("garbage\n")
    log.append({"type": "run", "pipeline": "b"})
    assert [e["pipeline"] for e in log.read()] == ["a", "b"]


def test_log_lives_in_the_brain_bookkeeping_folder_as_jsonl(brain):
    log = ActivityLog(brain)
    log.append({"type": "run", "pipeline": "a"})
    assert log.path == brain.config_dir / "log.jsonl"
    assert json.loads(log.path.read_text().splitlines()[0])["pipeline"] == "a"


def test_log_event_helpers(brain):
    log = ActivityLog(brain)
    log.event("pause", "a", trigger="cli")
    log.mcp_call("search")
    ev = log.read()
    assert ev[0] == {**ev[0], "type": "event", "action": "pause", "pipeline": "a", "trigger": "cli"}
    assert ev[1]["type"] == "mcp_call" and ev[1]["tool"] == "search" and "ts" in ev[1]
    assert "query" not in ev[1]


def test_read_of_missing_log_is_empty(brain):
    assert ActivityLog(brain).read() == []
