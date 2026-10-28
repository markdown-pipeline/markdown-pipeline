"""MD-12: the planner computes the exact change set without writing anything."""
import os

import pytest

from helpers import seed_owned
from mdpipeline.brain import init_brain
from mdpipeline.config import Pipeline, Source
from mdpipeline.frontmatter import content_hash
from mdpipeline.planner import plan_pipeline
from mdpipeline.state import State
import winsupport


@pytest.fixture
def brain(brain_dir):
    return init_brain(brain_dir)


def snapshot_tree(*roots):
    out = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            st = p.lstat()
            out[str(p)] = (p.read_bytes() if p.is_file() and not p.is_symlink() else None, st.st_mtime_ns)
    return out


def do_plan(brain, pipeline, state=None, **kw):
    state = state or State.load(brain)
    return plan_pipeline(brain, pipeline, state.pipeline(pipeline.name), now=1_790_000_000.0, **kw)


def actions(plan):
    return {(i.source_label, i.source_path): i.action for g in plan.groups for i in g.items}


def test_first_plan_creates_everything_with_target_paths(brain, make_source):
    src = make_source("docs", {"a.md": "A", "sub/b.md": "B"})
    plan = do_plan(brain, Pipeline.new("p", [str(src)]))
    items = {i.source_path: i for g in plan.groups for i in g.items}
    assert items["a.md"].action == "create" and items["a.md"].target == "raw/p/docs/a.md"
    assert items["sub/b.md"].target == "raw/p/docs/sub/b.md"
    assert plan.counts()["created"] == 2


def test_dry_run_writes_nothing_to_brain_or_sources(brain, brain_dir, make_source):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    before = snapshot_tree(brain_dir, src)
    do_plan(brain, Pipeline.new("p", [str(src)]))
    # the planner never touches the brain bookkeeping either
    assert snapshot_tree(brain_dir, src) == before


def test_each_item_has_source_label_path_target_action_and_reason_for_skips(brain, make_source):
    src = make_source("docs", {"a.md": "A", ".hidden/x.md": "X", "n.markdown": "N"})
    plan = do_plan(brain, Pipeline.new("p", [str(src)], include=["**/*.md", "**/*.markdown"]))
    items = {i.source_path: i for g in plan.groups for i in g.items}
    assert items[".hidden/x.md"].action == "skip" and "excluded by rule" in items[".hidden/x.md"].reason
    assert items["n.markdown"].action == "skip" and items["n.markdown"].reason == "not a .md file"
    assert items["a.md"].source_label == "docs"


def test_results_are_grouped_by_source_with_counts_per_group_and_total(brain, make_source):
    a = make_source("a", {"1.md": "1", "2.md": "2"})
    b = make_source("b", {"3.md": "3"})
    plan = do_plan(brain, Pipeline.new("p", [Source(str(a), "one"), Source(str(b), "two")]))
    assert [g.label for g in plan.groups] == ["one", "two"]
    assert [g.counts()["created"] for g in plan.groups] == [2, 1]
    assert plan.counts()["created"] == 3


def test_unchanged_updated_and_unowned_conflict_actions(brain, make_source):
    src = make_source("docs", {"same.md": "S", "changed.md": "new", "taken.md": "T"})
    pipe = Pipeline.new("p", [str(src)])
    st = State.load(brain)
    seed_owned(brain, st, "p", "docs", "same.md", b"S", "raw/p/docs/same.md", str(src))
    seed_owned(brain, st, "p", "docs", "changed.md", b"old", "raw/p/docs/changed.md", str(src))
    (brain.root / "raw/p/docs/taken.md").write_text("hand written")
    plan = do_plan(brain, pipe, st)
    a = actions(plan)
    assert a[("docs", "same.md")] == "unchanged"
    assert a[("docs", "changed.md")] == "update"
    assert a[("docs", "taken.md")] == "conflict"
    conflict = next(i for g in plan.groups for i in g.items if i.action == "conflict")
    assert conflict.conflict_type == "a" and "not owned" in conflict.reason


def test_brain_edited_owned_file_is_conflict_b(brain, make_source):
    src = make_source("docs", {"n.md": "N"})
    st = State.load(brain)
    seed_owned(brain, st, "p", "docs", "n.md", b"N", "raw/p/docs/n.md", str(src), brain_edit=b"I edited this")
    plan = do_plan(brain, Pipeline.new("p", [str(src)]), st)
    item = plan.groups[0].items[0]
    assert (item.action, item.conflict_type) == ("conflict", "b")


def test_force_turns_conflict_b_into_an_update_but_never_conflict_a(brain, make_source):
    src = make_source("docs", {"n.md": "N", "hand.md": "H"})
    st = State.load(brain)
    seed_owned(brain, st, "p", "docs", "n.md", b"N", "raw/p/docs/n.md", str(src), brain_edit=b"edited")
    (brain.root / "raw/p/docs/hand.md").write_text("mine")
    plan = do_plan(brain, Pipeline.new("p", [str(src)]), st, force=True)
    a = actions(plan)
    assert a[("docs", "n.md")] == "update" and a[("docs", "hand.md")] == "conflict"


def test_deleted_source_file_is_planned_as_delete_or_keep_per_policy(brain, make_source):
    src = make_source("docs", {"stay.md": "S"})
    for policy, expected in (("delete", "delete"), ("keep", "keep")):
        st = State.load(brain)
        seed_owned(brain, st, "p", "docs", "stay.md", b"S", "raw/p/docs/stay.md", str(src))
        seed_owned(brain, st, "p", "docs", "gone.md", b"G", "raw/p/docs/gone.md", str(src))
        plan = do_plan(brain, Pipeline.new("p", [str(src)], on_source_delete=policy), st)
        assert actions(plan)[("docs", "gone.md")] == expected
        if policy == "keep":
            gone = next(i for i in plan.groups[0].items if i.source_path == "gone.md")
            assert "source deleted" in gone.reason


def test_plan_reports_whether_the_guard_would_block_and_why(brain, make_source):
    src = make_source("docs", {})
    st = State.load(brain)
    for n in range(10):
        seed_owned(brain, st, "p", "docs", f"{n}.md", b"x", f"raw/p/docs/{n}.md", str(src))
    plan = do_plan(brain, Pipeline.new("p", [str(src)]), st)
    assert plan.counts()["deleted"] == 10
    assert plan.guard.blocked is True
    assert plan.guard.count == 10 and plan.guard.total == 10 and plan.guard.percent == 100.0
    assert "100%" in plan.guard.message and "10 of 10" in plan.guard.message


def test_plan_guard_not_blocking_when_small(brain, make_source):
    src = make_source("docs", {"a.md": "A"})
    plan = do_plan(brain, Pipeline.new("p", [str(src)]))
    assert plan.guard.blocked is False


def test_the_plan_does_not_depend_on_the_source_type(brain):
    """engine-core: the planner accepts pre-built scans from any source implementation."""
    from mdpipeline.sources import SourceEntry, SourceScan

    class FakeEntry(SourceEntry):
        def read_bytes(self):
            return b"from a connector"

    scan = SourceScan("saas", "saas://x", "ok", "", [FakeEntry("page.md", "saas://x/page", 1.0, 16)])
    pipe = Pipeline.new("p", [Source("/ignored", "saas")])
    st = State.load(brain)
    plan = plan_pipeline(brain, pipe, st.pipeline("p"), now=1.0, scans=[scan])
    assert plan.counts()["created"] == 1 and plan.groups[0].items[0].target == "raw/p/saas/page.md"


@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_unreadable_source_file_is_a_reported_error_not_a_crash(brain, make_source):
    src = make_source("docs", {"ok.md": "ok", "bad.md": "bad"})
    os.chmod(src / "bad.md", 0)
    try:
        plan = do_plan(brain, Pipeline.new("p", [str(src)]))
        if not winsupport.is_root():
            bad = next(i for i in plan.groups[0].items if i.source_path == "bad.md")
            assert bad.action == "skip" and bad.error and "cannot read" in bad.reason
    finally:
        os.chmod(src / "bad.md", 0o644)


def test_malformed_front_matter_is_a_reported_error_and_skipped(brain, make_source):
    src = make_source("docs", {"bad.md": "---\nkey: [oops\n---\nbody", "ok.md": "fine"})
    plan = do_plan(brain, Pipeline.new("p", [str(src)]))
    bad = next(i for i in plan.groups[0].items if i.source_path == "bad.md")
    assert bad.action == "skip" and bad.error and "front-matter" in bad.reason
    assert plan.counts()["errors"] == 1 and plan.counts()["created"] == 1


def test_change_set_id_is_stable_and_changes_when_a_source_changes(brain, make_source):
    src = make_source("docs", {"a.md": "A"})
    pipe = Pipeline.new("p", [str(src)])
    first = do_plan(brain, pipe).change_set_id
    assert do_plan(brain, pipe).change_set_id == first
    (src / "a.md").write_text("A changed")
    assert do_plan(brain, pipe).change_set_id != first


def test_in_run_target_collision_is_skipped_as_error_never_overwritten(brain, make_source):
    a = make_source("a", {"same.md": "1"})
    b = make_source("b", {"same.md": "2"})
    pipe = Pipeline.new("p", [Source(str(a), "one"), Source(str(b), "two")], target="raw/p/{relpath}")
    plan = do_plan(brain, pipe)
    acts = actions(plan)
    assert sorted(acts.values()) == ["create", "skip"]
    assert plan.counts()["errors"] == 1


def test_changed_template_moves_files_as_delete_plus_create(brain, make_source):
    src = make_source("docs", {"a.md": "A"})
    st = State.load(brain)
    seed_owned(brain, st, "p", "docs", "a.md", b"A", "raw/old/a.md", str(src))
    plan = do_plan(brain, Pipeline.new("p", [str(src)]), st)
    acts = {i.target: i.action for i in plan.items()}
    assert acts == {"raw/p/docs/a.md": "create", "raw/old/a.md": "delete"}


def test_a_source_file_that_errors_is_not_treated_as_deleted(brain, make_source):
    src = make_source("docs", {"bad.md": "---\nkey: [oops\n---\nbody"})
    st = State.load(brain)
    seed_owned(brain, st, "p", "docs", "bad.md", b"was fine", "raw/p/docs/bad.md", str(src))
    plan = do_plan(brain, Pipeline.new("p", [str(src)]), st)
    assert [i.action for i in plan.items()] == ["skip"]
    assert plan.counts()["deleted"] == 0
