"""MD-41: the Phase 2 data-loss scenarios for file-name policies and state recovery (release gate with test_data_loss.py).

    uv run pytest tests/test_data_loss.py tests/test_data_loss_names.py

Scenario numbers match the acceptance criteria of MD-41. End to end: real folders, the real engine, the real CLI and a
real SIGKILL. Sources must stay byte- and mtime-identical; nothing may be written outside raw/; hand-written files,
the wiki and files the pipeline does not own must survive every scenario. They run under whatever SEPARATOR is set in
mdpipeline.naming, so the owner's `-` or `_` decision does not change them.
"""
import os
import signal
import socket
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from conftest import tree_snapshot, write
from mdpipeline.cli import main
from mdpipeline.config import Config, Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_all, sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.naming import SEPARATOR as S
from mdpipeline.rebuild import apply_rebuild
from mdpipeline.recovery import apply_restore
from mdpipeline.service import ValidationFailed, create_pipeline
from mdpipeline.state import State

SRC = os.path.dirname(__file__) + "/../src"
DAY0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
POLICIES = [("original", ""), ("date-name", ""), ("name", ""), ("source-name", ""), ("date-source-name", ""),
            ("custom", "{pipeline}.{slug}.{date}")]


def at(days: int) -> float:
    return (DAY0 + timedelta(days=days)).timestamp()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture
def world(brain, brain_dir, make_source):
    """A brain with curated content and a source folder in the styles of a real project (dated, undated, Hebrew)."""
    write(brain_dir / "wiki/index.md", "# Curated index\n")
    write(brain_dir / "work/tool.py", "print('mine')\n")
    write(brain_dir / "raw/handwritten/note.md", "# hand written raw note\n")
    docs = make_source("docs", {
        "2026-10-01_phase1-prd.md": "# PRD\n", "20260930-seed-idea.md": "# Seed\n", "README.md": "# Readme\n",
        "Meeting Notes (3.10).md": "---\ntitle: Meeting\n---\nnotes\n", "סיכום פגישה.md": "# סיכום\n",
        "deep/er/Nested File.md": "nested\n", ".claude/skills/s.md": "excluded\n",
    })
    return {"brain": brain, "root": brain_dir, "docs": docs}


def guarded_tree(w) -> dict:
    r = w["root"]
    return {"wiki": tree_snapshot(r / "wiki"), "work": tree_snapshot(r / "work"), "hand": tree_snapshot(r / "raw/handwritten")}


def raw_md(w, folder="raw/p") -> list[str]:
    return sorted(str(p.relative_to(w["root"] / folder)) for p in (w["root"] / folder).rglob("*.md"))


def add(w, **kw):
    cfg = load_config(w["brain"])
    cfg.pipelines.append(Pipeline.new("p", [Source(str(w["docs"]), "docs")], target="raw/p/{relpath}", **kw))
    save_config(w["brain"], cfg)


def switch(w, policy, pattern=""):
    cfg = load_config(w["brain"])
    cfg.get("p").name_policy, cfg.get("p").name_pattern = policy, pattern
    save_config(w["brain"], cfg)


# 1. Upgrade from a P1 brain: 0 renames, 0 content changes ---------------------------------------------------
def test_m1_upgrading_a_p1_brain_renames_nothing_and_changes_no_content(world):
    b = world["brain"]
    add(world)                                                  # P1 pipeline: no name policy at all
    assert sync_pipeline(b, "p", now=at(0)).result == "ok"
    cfg = b.config_path.read_text(encoding="utf-8")
    assert "name_policy" not in cfg                             # the config a P1 brain has
    state_text = (b.config_dir / "state.json").read_text(encoding="utf-8")
    before = tree_snapshot(world["root"] / "raw")
    for step in (lambda: sync_pipeline(b, "p", now=at(30)), lambda: sync_pipeline(b, "p", trigger="mcp", now=at(31)),
                 lambda: sync_all(b), lambda: main(["--brain", str(world["root"]), "sync", "p"]),
                 lambda: main(["--brain", str(world["root"]), "pipeline", "edit", "p", "--guard-percent", "50"]),
                 lambda: main(["--brain", str(world["root"]), "sync", "p"])):
        step()
        assert tree_snapshot(world["root"] / "raw") == before        # not one rename, not one byte, not one mtime
    dry = sync_pipeline(b, "p", dry_run=True, now=at(60))
    assert dry.counts()["unchanged"] == 6 and sum(dry.counts()[k] for k in ("created", "updated", "renamed", "deleted")) == 0
    assert load_config(b).get("p").name_policy == "original"
    assert all(r.first_seen is None and r.name_policy is None for r in State.load(b).pipeline("p").files.values())
    assert state_text.count('"raw/p/') == 6


# 2. Policy change with the guard -----------------------------------------------------------------------------
@pytest.fixture
def switched(world):
    add(world)
    sync_pipeline(world["brain"], "p", now=at(0))
    switch(world, "date-name")
    return world


def test_m2_above_the_threshold_nothing_is_written_and_approval_covers_only_the_shown_plan(switched):
    w = switched
    b = w["brain"]
    raw_before, guarded = tree_snapshot(w["root"] / "raw"), guarded_tree(w)
    blocked = sync_pipeline(b, "p", now=at(5), trigger="cli")
    assert blocked.result == "blocked" and blocked.counts()["renamed"] >= 5
    assert tree_snapshot(w["root"] / "raw") == raw_before
    assert sync_pipeline(b, "p", now=at(5), trigger="mcp", approve=True).result == "blocked"       # the MCP cannot approve
    write(w["docs"] / "README.md", "# Readme, edited after the plan was shown\n")
    assert sync_pipeline(b, "p", now=at(5), trigger="cli", approve=True).result == "blocked"       # another plan: not approved
    assert tree_snapshot(w["root"] / "raw") == raw_before
    shown = sync_pipeline(b, "p", now=at(5), trigger="cli")
    assert shown.result == "blocked"
    done = sync_pipeline(b, "p", now=at(5), trigger="cli", approve=True)
    assert done.result == "ok" and done.counts()["renamed"] >= 5
    assert guarded_tree(w) == guarded
    assert f"2026-10-04{S}readme.md" in raw_md(w)


def test_m2_sigkill_in_the_middle_of_a_rename_converges(world):
    w = world
    b = w["brain"]
    for i in range(12):
        write(w["docs"] / f"bulk/Item {i}.md", f"# item {i}\n" * 200)
    add(w)
    sync_pipeline(b, "p", now=at(0))
    switch(w, "date-name")
    assert sync_pipeline(b, "p", now=at(2), trigger="cli").result == "blocked"          # the rename plan is shown
    guarded = guarded_tree(w)
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {SRC!r})
        from mdpipeline.brain import find_brain
        from mdpipeline.engine import sync_pipeline
        n = [0]
        def hook(item):
            n[0] += 1
            if n[0] == 6:
                os.kill(os.getpid(), 9)
        sync_pipeline(find_brain({str(w['root'])!r}), "p", now={at(2)}, trigger="cli", approve=True, after_write=hook)
    """)
    assert subprocess.run([sys.executable, "-c", script]).returncode == -signal.SIGKILL
    assert [p for p in w["root"].rglob("*.tmp")] == []
    for _ in range(3):                                                    # re-running converges (a changed plan is re-shown)
        res = sync_pipeline(b, "p", now=at(2), trigger="cli", approve=True)
        if res.result != "blocked":
            break
    assert res.result == "ok"
    names = raw_md(w)
    assert len(names) == len(set(n.casefold() for n in names)) == 18        # 6 sources + 12 bulk, no duplicates
    assert all(any(d in n for d in ("2026-10-04", "2026-10-01", "2026-09-30")) for n in names)     # first-seen kept, not today's
    assert sorted(str(p.relative_to(w["root"])) for p in (w["root"] / "raw/p").rglob("*.md")) == sorted(State.load(b).pipeline("p").files)
    assert sync_pipeline(b, "p", now=at(3)).counts()["unchanged"] == 18
    assert guarded_tree(w) == guarded


# 3. Clashes -------------------------------------------------------------------------------------------------
@pytest.fixture
def clashing(brain, brain_dir, make_source):
    write(brain_dir / "raw/handwritten/note.md", "mine\n")
    a = make_source("a", {"Notes.md": "from a\n", "café.md": "composed\n"})
    c = make_source("c", {"notes.md": "from c\n", "café.md": "decomposed\n"})
    cfg = Config([Pipeline("p", [Source(str(a), "a"), Source(str(c), "c")], target="raw/p/{relpath}", name_policy="date-name")])
    save_config(brain, cfg)
    return {"brain": brain, "root": brain_dir, "a": a, "c": c}


def test_m3_case_only_and_normalization_only_clashes_never_overwrite_and_suffixes_are_stable(clashing):
    w = clashing
    b = w["brain"]
    assert sync_pipeline(b, "p", now=at(0)).result == "ok"
    names = sorted(p.name for p in (w["root"] / "raw/p").glob("*.md"))
    assert len(names) == 4 and len({n.casefold() for n in names}) == 4
    bodies = sorted(parse_document(p.read_text(encoding="utf-8"))[1] for p in (w["root"] / "raw/p").glob("*.md"))
    assert bodies == sorted(["from a\n", "from c\n", "composed\n", "decomposed\n"])        # no source lost or overwritten
    for day in (1, 9, 40):                                                                  # stable across re-syncs
        assert sync_pipeline(b, "p", now=at(day)).counts()["unchanged"] == 4
    write(w["c"] / "notes.md", "from c, edited\n")
    sync_pipeline(b, "p", now=at(41))
    assert sorted(p.name for p in (w["root"] / "raw/p").glob("*.md")) == names
    (w["a"] / "Notes.md").unlink()                                                          # the other file is deleted
    (w["a"] / "café.md").unlink()
    sync_pipeline(b, "p", now=at(42))
    left = sorted(p.name for p in (w["root"] / "raw/p").glob("*.md"))
    assert len(left) == 2 and all(n in names for n in left)                                 # suffixes stay, names never swap
    assert sync_pipeline(b, "p", now=at(43)).counts()["unchanged"] == 2
    assert (w["root"] / "raw/handwritten/note.md").read_text(encoding="utf-8") == "mine\n"


def test_m3_a_hand_written_file_at_the_clean_name_is_never_overwritten(clashing):
    w = clashing
    mine = write(w["root"] / f"raw/p/2026-10-04{S}notes.md", "my own note, not the pipeline's\n")
    snap = tree_snapshot(mine)
    res = sync_pipeline(w["brain"], "p", now=at(0))
    assert tree_snapshot(mine) == snap and mine.read_text(encoding="utf-8") == "my own note, not the pipeline's\n"
    assert res.counts()["conflicts"] >= 1


# 4. Edits ---------------------------------------------------------------------------------------------------
def test_m4_ten_edits_keep_the_name_and_the_first_seen_date(world):
    b = world["brain"]
    add(world, name_policy="date-name")
    sync_pipeline(b, "p", now=at(0))
    names = raw_md(world)
    for n in range(1, 11):
        write(world["docs"] / "README.md", f"# Readme, edit {n}\n")
        assert sync_pipeline(b, "p", now=at(n * 3)).result == "ok"
    assert raw_md(world) == names
    rec = State.load(b).pipeline("p").files[f"raw/p/2026-10-04{S}readme.md"]
    assert rec.first_seen == "2026-10-04"
    meta, body = parse_document((world["root"] / f"raw/p/2026-10-04{S}readme.md").read_text(encoding="utf-8"))
    assert body == "# Readme, edit 10\n" and meta["provenance"]["first_seen"] == "2026-10-04"


# 5. Hostile custom patterns -----------------------------------------------------------------------------------
@pytest.mark.parametrize("pattern", ["../{slug}", "/{slug}", "a/{slug}", "..{slug}", "{date}", "{slug}/../x", "{x}{slug}", "{slug}\\x"])
def test_m5_hostile_patterns_are_rejected_at_save_and_nothing_is_written(world, pattern, capsys):
    b = world["brain"]
    before = (b.config_path.read_bytes(), tree_snapshot(world["root"]))
    with pytest.raises(ValidationFailed):
        create_pipeline(b, Pipeline.new("p", [str(world["docs"])], name_policy="custom", name_pattern=pattern))
    code = main(["--brain", str(world["root"]), "pipeline", "add", "q", "--source", f"docs={world['docs']}", "--name-pattern", pattern])
    assert code == 2
    assert (b.config_path.read_bytes(), tree_snapshot(world["root"])) == before


@pytest.mark.parametrize("pattern", ["../../../wiki/{slug}", "../{slug}", "/tmp/{slug}", "{date}"])
def test_m5_even_a_hand_edited_config_cannot_write_outside_raw(world, pattern):
    w = world
    add(w, name_policy="custom", name_pattern=pattern)           # bypasses save-time validation, like a hand-edited file
    guarded = guarded_tree(w)
    outside = tree_snapshot(w["root"].parent / "sources")
    sync_pipeline(w["brain"], "p", now=at(0))
    sync_pipeline(w["brain"], "p", now=at(1), trigger="mcp")
    assert guarded_tree(w) == guarded
    assert tree_snapshot(w["root"].parent / "sources") == outside
    for p in w["root"].rglob("*"):
        if p.is_file() and ".mdpipeline" not in p.parts and "raw" not in p.parts and "wiki" not in p.parts and "work" not in p.parts:
            raise AssertionError(f"unexpected file outside raw/: {p}")
    assert not list(w["root"].parent.glob("*.md")) and not os.path.exists("/tmp/readme.md")


# 6. State corruption --------------------------------------------------------------------------------------------
@pytest.fixture
def owned(world):
    add(world, name_policy="date-name")
    sync_pipeline(world["brain"], "p", now=at(0))
    sync_pipeline(world["brain"], "p", now=at(1))
    return world


def test_m6_restore_from_backup_loses_no_file_and_owns_them_all_again(owned):
    w = owned
    b = w["brain"]
    raw_before, guarded = tree_snapshot(w["root"] / "raw"), guarded_tree(w)
    (b.config_dir / "state.json").write_text("{corrupt")
    with pytest.raises(Exception):
        sync_pipeline(b, "p")                                         # still stops: no empty-state fallback
    assert tree_snapshot(w["root"] / "raw") == raw_before
    apply_restore(b)
    res = sync_pipeline(b, "p", now=at(2))
    assert res.result == "ok" and res.counts()["unchanged"] == 6 and res.counts()["conflicts"] == 0
    assert tree_snapshot(w["root"] / "raw") == raw_before and guarded_tree(w) == guarded


def test_m6_rebuild_never_claims_a_hand_written_file(owned):
    w = owned
    b = w["brain"]
    real = (w["root"] / f"raw/p/2026-10-04{S}readme.md").read_text(encoding="utf-8")
    forged = write(w["root"] / "raw/p/my-readme.md", real.replace("# Readme", "# I wrote this"))
    plain = write(w["root"] / "raw/p/plain-note.md", "no provenance\n")
    edited = w["root"] / f"raw/p/2026-09-30{S}seed-idea.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "edited in the brain\n")
    (b.config_dir / "state.json").unlink()
    for f in (b.config_dir / "backups").glob("*"):
        f.unlink()
    raw_before = tree_snapshot(w["root"] / "raw")
    apply_rebuild(b)
    owned_now = State.load(b).pipeline("p").files
    for path in ("raw/p/my-readme.md", "raw/p/plain-note.md", str(edited.relative_to(w["root"]))):
        assert path not in owned_now
    assert len(owned_now) == 5
    assert tree_snapshot(w["root"] / "raw") == raw_before                      # the rebuild writes nothing to raw/
    res = sync_pipeline(b, "p", now=at(5))
    assert forged.read_text(encoding="utf-8").count("I wrote this") == 1 and plain.read_text(encoding="utf-8") == "no provenance\n"
    assert "edited in the brain" in edited.read_text(encoding="utf-8")
    assert res.counts()["unchanged"] == 5


# 7. Source immutability and idempotence under every policy ----------------------------------------------------------
@pytest.mark.parametrize("policy, pattern", POLICIES)
@pytest.mark.parametrize("kind", ["dry_run", "sync", "resync", "force", "all", "mcp_trigger", "after_delete", "policy_switch"])
def test_m7_s1_sources_stay_byte_and_mtime_identical_under_every_policy(world, policy, pattern, kind):
    b = world["brain"]
    add(world, name_policy=policy, name_pattern=pattern)
    sources = tree_snapshot(world["docs"])
    if kind == "dry_run":
        sync_pipeline(b, "p", dry_run=True, now=at(0))
    elif kind == "sync":
        sync_pipeline(b, "p", now=at(0))
    elif kind == "resync":
        sync_pipeline(b, "p", now=at(0)); sync_pipeline(b, "p", now=at(3))
    elif kind == "force":
        sync_pipeline(b, "p", now=at(0))
        for f in (world["root"] / "raw/p").rglob("README*.md"):
            f.write_text("edit")
        sync_pipeline(b, "p", now=at(1), force=True)
    elif kind == "all":
        sync_all(b)
    elif kind == "mcp_trigger":
        sync_pipeline(b, "p", trigger="mcp", now=at(0))
    elif kind == "after_delete":
        sync_pipeline(b, "p", now=at(0))
        for f in (world["root"] / "raw/p").rglob("README*.md"):
            f.unlink()
        sync_pipeline(b, "p", now=at(1))
    else:
        sync_pipeline(b, "p", now=at(0))
        switch(world, "name")
        sync_pipeline(b, "p", now=at(1), trigger="cli", approve=True)
        sync_pipeline(b, "p", now=at(1), trigger="cli", approve=True)
    assert tree_snapshot(world["docs"]) == sources


@pytest.mark.parametrize("policy, pattern", POLICIES)
def test_m7_s10_a_second_sync_with_no_changes_makes_zero_writes_under_every_policy(world, policy, pattern):
    b = world["brain"]
    add(world, name_policy=policy, name_pattern=pattern)
    assert sync_pipeline(b, "p", now=at(0)).result == "ok"
    snap, state = tree_snapshot(world["root"] / "raw"), (b.config_dir / "state.json").read_bytes()
    res = sync_pipeline(b, "p", now=at(20))
    assert tree_snapshot(world["root"] / "raw") == snap
    assert sum(res.counts()[k] for k in ("created", "updated", "renamed", "deleted")) == 0
    assert (b.config_dir / "state.json").read_bytes() == state


@pytest.mark.parametrize("policy, pattern", POLICIES)
def test_m7_only_md_files_inside_raw_are_written_and_the_curated_areas_survive(world, policy, pattern):
    add(world, name_policy=policy, name_pattern=pattern)
    guarded = guarded_tree(world)
    sync_pipeline(world["brain"], "p", now=at(0))
    assert guarded_tree(world) == guarded
    written = [p for p in (world["root"] / "raw/p").rglob("*") if p.is_file()]
    assert written and all(p.suffix == ".md" for p in written)
