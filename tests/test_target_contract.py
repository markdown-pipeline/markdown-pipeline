"""MD-60: the engine plans, guards, writes, owns and recovers through one target contract."""
import json
from pathlib import Path

import pytest

from conftest import write
from memory_target import MemoryTarget
from mdpipeline.config import Pipeline
from mdpipeline.engine import sync_pipeline
from mdpipeline.state import State
from mdpipeline.target import FileRead, LocalTarget, Target, TargetError


def test_local_target_reads_absent_file_and_other(brain, brain_dir):
    write(brain_dir / "raw" / "a.md", "A")
    (brain_dir / "raw" / "d").mkdir()
    t = brain.target
    assert isinstance(t, LocalTarget) and isinstance(t, Target)
    assert t.read_file("raw/a.md") == FileRead("file", b"A")
    assert t.read_file("raw/nope.md") == FileRead("absent", None)
    assert t.read_file("raw/d").kind == "other"


def test_local_target_state_round_trips_through_the_contract(brain):
    assert brain.target.read_state() is None
    st = State.load(brain)
    st.pipeline("p")
    st.save()
    assert json.loads(brain.target.read_state())["pipelines"] == {"p": {"files": {}, "pending_approval": None, "blocked": False}}


@pytest.fixture
def mem(brain, add_pipeline, make_source):
    target = MemoryTarget()
    brain.use_target(target)
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    return target


def test_engine_syncs_through_a_non_local_target_and_commits_once(brain, brain_dir, mem):
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 2
    assert sorted(f for f in mem.files if f.endswith(".md")) == ["raw/p/docs/a.md", "raw/p/docs/b.md"]
    assert len(mem.commits) == 1 and [c for c in mem.calls if c[0] == "commit"].__len__() == 1
    assert list((brain_dir / "raw").iterdir()) == []            # nothing reached the local folder
    assert set(json.loads(mem.state)["pipelines"]["p"]["files"]) == {"raw/p/docs/a.md", "raw/p/docs/b.md"}


def test_second_sync_makes_no_commit(brain, mem):
    sync_pipeline(brain, "p", trigger="cli")
    sync_pipeline(brain, "p", trigger="cli")
    assert len(mem.commits) == 1


def test_dry_run_never_writes_or_commits(brain, mem):
    res = sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert res.counts()["created"] == 2
    assert not [c for c in mem.calls if c[0] in ("write", "delete", "commit")]


@pytest.mark.parametrize("template", ["wiki/{relpath}", "raw/../wiki/{relpath}", "../x/{relpath}", "raw/{pipeline}/{name}.txt"])
def test_boundary_is_enforced_in_the_engine_for_every_target(brain, add_pipeline, make_source, template):
    target = MemoryTarget()
    brain.use_target(target)
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)], target=template))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error"
    assert not [c for c in target.calls if c[0] == "write"]


def test_a_read_failure_is_an_error_never_a_deletion(brain, mem):
    sync_pipeline(brain, "p", trigger="cli")
    before = dict(mem.files)
    mem.fail_reads = True
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and "unreachable" in res.message
    assert mem.files == before and not [c for c in mem.calls[-5:] if c[0] == "delete"]


def test_a_commit_failure_leaves_the_target_unchanged_and_the_next_sync_converges(brain, mem):
    mem.fail_commit = TargetError("network down")
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "error" and mem.files == {} and mem.state is None
    mem.fail_commit = None
    assert sync_pipeline(brain, "p", trigger="cli").counts()["created"] == 2


def test_the_contract_is_documented():
    doc = (Path(__file__).parent.parent / "docs" / "target-contract.md").read_text()
    for name in ("read_file", "list_markdown", "read_state", "write_state", "write", "delete", "commit", "begin",
                 "ConcurrentChange", "TargetError", "never read as"):
        assert name in doc


def test_a_guard_blocked_sync_makes_no_commit(brain, add_pipeline, make_source):
    target = MemoryTarget()
    brain.use_target(target)
    src = make_source("docs", {f"{i}.md": str(i) for i in range(10)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    for i in range(10):
        (src / f"{i}.md").unlink()
    commits = len(target.commits)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "blocked" and len(target.commits) == commits
    assert "pending_approval" not in json.loads(target.state)["pipelines"]["p"]       # approvals stay local
    approved = sync_pipeline(brain, "p", trigger="cli", approve=True)
    assert approved.result == "ok" and len(target.commits) == commits + 1
    assert "change-set: cs-" in target.commits[-1] and target.commits[-1].startswith("mdpipeline sync [p]: 0 created, 0 updated, 0 renamed, 10 deleted")
