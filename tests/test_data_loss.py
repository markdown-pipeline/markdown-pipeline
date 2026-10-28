"""MD-30: the Phase 1 data-loss scenario suite (release gate). Run alone with:

    uv run pytest tests/test_data_loss.py

Scenario numbers match the acceptance criteria of MD-30. These are end-to-end: real folders, the real
engine and the real CLI. Source folders must be byte- and mtime-identical after everything; nothing
may be written outside raw/; hand-written and brain-edited notes must survive.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from conftest import tree_snapshot, write
from mdpipeline.cli import main
from mdpipeline.config import Pipeline, Source, load_config, save_config
from mdpipeline.engine import sync_all, sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.service import ValidationFailed, create_pipeline

SRC = os.path.dirname(__file__) + "/../src"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Definition of Done 4: no network calls in any test of this suite."""
    def deny(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture
def world(brain, brain_dir, make_source, add_pipeline):
    """A brain with curated wiki/work content and two realistic source folders (one pipeline, two folders)."""
    write(brain_dir / "wiki/index.md", "# Curated index\n")
    write(brain_dir / "wiki/concepts/x.md", "curated concept\n")
    write(brain_dir / "work/tool.py", "print('mine')\n")
    write(brain_dir / "raw/handwritten/note.md", "# hand written raw note\n")
    docs = make_source("docs", {
        "pricing.md": "---\ntitle: Pricing\ntags: [a, b]\n---\n# Pricing\n",
        "plain.md": "# Plain\nno front matter\n",
        "deep/er/nested.md": "nested\n",
        ".claude/skills/s.md": "agent config, excluded\n",
        "node_modules/p/readme.md": "excluded\n",
    })
    specs = make_source("specs", {"spec.md": "# Spec\n", "bad.md": "---\nkey: [oops\n---\nbody\n"})
    add_pipeline(Pipeline.new("biz", [Source(str(docs), "docs"), Source(str(specs), "specs")]))
    return {"brain": brain, "root": brain_dir, "docs": docs, "specs": specs}


def protected(w):
    r = w["root"]
    return {"wiki": tree_snapshot(r / "wiki"), "work": tree_snapshot(r / "work"), "hand": tree_snapshot(r / "raw/handwritten")}


def assert_protected(w, snap):
    r = w["root"]
    assert tree_snapshot(r / "wiki") == snap["wiki"]
    assert tree_snapshot(r / "work") == snap["work"]
    assert tree_snapshot(r / "raw/handwritten") == snap["hand"]


# 1. Source immutability ---------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["dry_run", "sync", "resync", "force", "all", "mcp_trigger", "after_delete"])
def test_s1_source_folders_are_byte_and_mtime_identical_after_every_sync_type(world, kind):
    b = world["brain"]
    sources = tree_snapshot(world["docs"], world["specs"])
    if kind == "dry_run":
        sync_pipeline(b, "biz", dry_run=True)
    elif kind == "sync":
        sync_pipeline(b, "biz")
    elif kind == "resync":
        sync_pipeline(b, "biz"); sync_pipeline(b, "biz")
    elif kind == "force":
        sync_pipeline(b, "biz")
        (b.root / "raw/biz/docs/plain.md").write_text("edit")
        sync_pipeline(b, "biz", force=True)
    elif kind == "all":
        sync_all(b)
    elif kind == "mcp_trigger":
        sync_pipeline(b, "biz", trigger="mcp")
    else:
        sync_pipeline(b, "biz")
        sources = tree_snapshot(world["docs"], world["specs"])
        (b.root / "raw/biz/docs/plain.md").unlink()   # a brain-side deletion must not reach the source
        sync_pipeline(b, "biz")
    assert tree_snapshot(world["docs"], world["specs"]) == sources


def test_s1_cli_sync_leaves_sources_untouched(world, capsys):
    sources = tree_snapshot(world["docs"], world["specs"])
    assert main(["--brain", str(world["root"]), "sync", "biz"]) in (0, 1)  # bad.md is an expected error
    assert tree_snapshot(world["docs"], world["specs"]) == sources


# 2. Write boundary --------------------------------------------------------------------------
HOSTILE = ["../evil/{relpath}", "raw/../wiki/{relpath}", "wiki/{relpath}", ".mdpipeline/{relpath}",
           "raw/{pipeline}/../../wiki/{relpath}"]          # MD-133: "{relpath}", "work/..." and an absolute-looking path stay inside the folder: allowed


@pytest.mark.parametrize("template", HOSTILE)
def test_s2_hostile_templates_write_nothing_outside_raw_and_nothing_to_wiki(world, template):
    snap = protected(world)
    parent_before = sorted(os.listdir(world["root"].parent))
    cfg = load_config(world["brain"])
    cfg.pipelines[0].target = template
    save_config(world["brain"], cfg)           # bypasses validation: the write boundary is the last defence
    sync_pipeline(world["brain"], "biz")
    assert_protected(world, snap)
    assert sorted(os.listdir(world["root"].parent)) == parent_before
    assert not os.path.exists("/tmp/mdp-abs-pricing.md")


def test_s2_hostile_templates_are_also_rejected_at_save_time(world, make_source):
    other = make_source("other", {"a.md": "x"})
    for t in HOSTILE:
        with pytest.raises(ValidationFailed):
            create_pipeline(world["brain"], Pipeline.new("hostile", [str(other)], target=t))


def test_s2_symlinked_target_folders_and_hostile_names_and_labels(world, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    snap = protected(world)
    os.symlink(world["root"] / "wiki", world["root"] / "raw/biz")      # target folder symlinked into wiki
    sync_pipeline(world["brain"], "biz")
    os.unlink(world["root"] / "raw/biz")
    os.symlink(outside, world["root"] / "raw/biz")                     # ... and out of the brain
    sync_pipeline(world["brain"], "biz")
    os.unlink(world["root"] / "raw/biz")
    cfg = load_config(world["brain"])
    cfg.pipelines[0].sources[0].label = "../../wiki"
    cfg.pipelines[0].sources[1].label = "a/b"
    save_config(world["brain"], cfg)
    sync_pipeline(world["brain"], "biz")
    write(world["docs"] / "weird..name.md", "x")
    cfg.pipelines[0].sources[0].label = "docs"
    cfg.pipelines[0].sources[1].label = "specs"
    save_config(world["brain"], cfg)
    sync_pipeline(world["brain"], "biz")
    assert_protected(world, snap)
    assert list(outside.iterdir()) == []


def test_s2_only_md_files_exist_among_everything_written(world):
    sync_pipeline(world["brain"], "biz")
    written = [p for p in (world["root"] / "raw/biz").rglob("*") if p.is_file()]
    assert written and all(p.suffix == ".md" for p in written)


# 3. Ownership ------------------------------------------------------------------------------
def test_s3_hand_written_notes_are_never_modified_or_deleted_and_brain_edits_need_force(world):
    b, root = world["brain"], world["root"]
    hand = root / "raw/biz/docs/plain.md"
    write(hand, "MY OWN NOTE at a colliding path\n")
    sync_pipeline(b, "biz")
    (world["docs"] / "plain.md").unlink()
    sync_pipeline(b, "biz", force=True)
    assert hand.read_text() == "MY OWN NOTE at a colliding path\n"       # conflict (a): never forced, never deleted
    pricing = root / "raw/biz/docs/pricing.md"
    pricing.write_text(pricing.read_text() + "\nbrain edit\n")
    (world["docs"] / "pricing.md").write_text("# Pricing v2\n")
    res = sync_pipeline(b, "biz")
    assert "brain edit" in pricing.read_text() and "v2" not in pricing.read_text()
    assert any(c["type"] == "b" for c in res.conflicts)
    sync_pipeline(b, "biz", force=True)
    assert "v2" in pricing.read_text() and "brain edit" not in pricing.read_text()
    assert (root / "raw/handwritten/note.md").read_text() == "# hand written raw note\n"


# 4. Deletion policy ------------------------------------------------------------------------
def test_s4_delete_policy_renames_and_unowned_files(world):
    b, root = world["brain"], world["root"]
    sync_pipeline(b, "biz")
    write(root / "raw/biz/docs/unowned-next-to-owned.md", "mine")
    os.rename(world["docs"] / "plain.md", world["docs"] / "renamed.md")
    (world["specs"] / "spec.md").unlink()
    res = sync_pipeline(b, "biz")
    assert (root / "raw/biz/docs/renamed.md").exists() and not (root / "raw/biz/docs/plain.md").exists()
    assert not (root / "raw/biz/specs/spec.md").exists()
    assert (root / "raw/biz/docs/unowned-next-to-owned.md").read_text() == "mine"
    assert res.counts()["deleted"] == 2


def test_s4_keep_policy_keeps_everything(world):
    b, root = world["brain"], world["root"]
    cfg = load_config(b)
    cfg.pipelines[0].on_source_delete = "keep"
    save_config(b, cfg)
    sync_pipeline(b, "biz")
    shutil.rmtree(world["docs"] / "deep")
    os.rename(world["docs"] / "plain.md", world["docs"] / "renamed.md")
    res = sync_pipeline(b, "biz")
    assert (root / "raw/biz/docs/deep/er/nested.md").exists() and (root / "raw/biz/docs/plain.md").exists()
    assert res.counts()["deleted"] == 0 and res.counts()["kept"] == 2


# 5. Missing folder -------------------------------------------------------------------------
def test_s5_unmounted_or_renamed_source_causes_zero_deletions_and_the_others_sync(world, tmp_path):
    b, root = world["brain"], world["root"]
    sync_pipeline(b, "biz")
    owned_before = sorted(str(p) for p in (root / "raw/biz/docs").rglob("*.md"))
    shutil.move(str(world["docs"]), str(tmp_path / "renamed-docs"))
    (world["specs"] / "new.md").write_text("new")
    res = sync_pipeline(b, "biz")
    assert res.result == "error"
    assert sorted(str(p) for p in (root / "raw/biz/docs").rglob("*.md")) == owned_before
    assert (root / "raw/biz/specs/new.md").exists()
    assert res.counts()["deleted"] == 0


def test_s5_every_source_missing_deletes_nothing(world, tmp_path):
    b, root = world["brain"], world["root"]
    sync_pipeline(b, "biz")
    before = tree_snapshot(root / "raw")
    shutil.move(str(world["docs"]), str(tmp_path / "d"))
    shutil.move(str(world["specs"]), str(tmp_path / "s"))
    res = sync_pipeline(b, "biz")
    assert res.result == "error" and tree_snapshot(root / "raw") == before


# 7. Crash safety ---------------------------------------------------------------------------
def test_s7_sigkill_mid_run_leaves_no_partial_files_and_rerun_converges(world):
    b = world["brain"]
    for i in range(30):
        write(world["docs"] / f"bulk/n{i}.md", f"# n{i}\n" * 300)
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {SRC!r})
        from mdpipeline.brain import find_brain
        from mdpipeline.engine import sync_pipeline
        n = [0]
        def hook(item):
            n[0] += 1
            if n[0] == 7:
                os.kill(os.getpid(), 9)
        sync_pipeline(find_brain({str(world['root'])!r}), "biz", after_write=hook)
    """)
    assert subprocess.run([sys.executable, "-c", script]).returncode == -signal.SIGKILL
    leftovers = [p for p in world["root"].rglob("*") if p.suffix == ".tmp"]
    assert leftovers == []
    for f in (world["root"] / "raw/biz").rglob("*.md"):
        meta, body = parse_document(f.read_text())
        assert meta["provenance"]["pipeline"] == "biz" and body
    res = sync_pipeline(b, "biz")
    assert res.counts()["conflicts"] == 0
    assert sync_pipeline(b, "biz").counts()["created"] == 0
    assert len(list((world["root"] / "raw/biz/docs/bulk").glob("*.md"))) == 30


# 8. Front-matter merge ---------------------------------------------------------------------
SAMPLES = {
    "none.md": "# Just a heading\n\nbody\n",
    "empty-fm.md": "---\n---\nbody\n",
    "rich.md": "---\ntitle: \"A: B\"\ntags:\n  - x\n  - y\nmeta: {k: [1, 2], z: null}\nmulti: |\n  a\n  b\ndate: 2026-01-02\nflag: yes\n---\n# T\n",
    "crlf.md": "---\r\ntitle: win\r\n---\r\nline\r\n",
    "bom.md": "﻿---\ntitle: bom\n---\nx\n",
    "unicode.md": "---\nכותרת: שלום\n---\nגוף ✓\n",
    "hr-body.md": "# T\n\n---\n\nbody after a rule\n",
    "no-trailing-newline.md": "---\na: 1\n---\nno newline at end",
}
MALFORMED = {
    "unterminated.md": "---\ntitle: x\nbody without end\n",
    "bad-yaml.md": "---\nkey: [oops\n---\nbody\n",
    "list-fm.md": "---\n- a\n- b\n---\nbody\n",
    "has-provenance.md": "---\nprovenance:\n  pipeline: other\n---\nbody\n",
}


def test_s8_author_keys_survive_varied_samples_and_malformed_files_are_skipped_not_fatal(brain, brain_dir, make_source, add_pipeline):
    import yaml

    src = make_source("samples", {**SAMPLES, **MALFORMED})
    add_pipeline(Pipeline.new("s", [str(src)]))
    before = tree_snapshot(src)
    res = sync_pipeline(brain, "s")
    assert tree_snapshot(src) == before
    assert res.counts()["errors"] == len(MALFORMED) and res.counts()["created"] == len(SAMPLES)
    for name, text in SAMPLES.items():
        out = (brain_dir / "raw/s/samples" / name).read_bytes().decode()
        meta, body = parse_document(out)
        orig_meta, orig_body = parse_document(text)
        assert {k: v for k, v in meta.items() if k != "provenance"} == orig_meta, name
        assert body == orig_body, name
        assert out.endswith(orig_body.encode().decode()), name
        assert meta["provenance"]["pipeline"] == "s" and meta["provenance"]["source_path"] == name
    for name in MALFORMED:
        assert not (brain_dir / "raw/s/samples" / name).exists()
    # the skipped files are still intact at the source and a later fix is picked up
    (src / "bad-yaml.md").write_text("---\nkey: fixed\n---\nbody\n")
    assert sync_pipeline(brain, "s").counts()["created"] == 1


# 9. Configuration validation ---------------------------------------------------------------
def test_s9_validation_rejections(world, make_source, brain_dir):
    b = world["brain"]
    inner = brain_dir / "raw" / "inner"
    inner.mkdir()
    cases = {
        "source inside raw/": [str(inner)],
        "source containing the brain": [str(brain_dir.parent)],
        "duplicate folders": [str(world["docs"]), str(world["docs"])],
        "nested folders": [str(world["docs"]), str(world["docs"] / "deep")],
    }
    for label, sources in cases.items():
        with pytest.raises(ValidationFailed):
            create_pipeline(b, Pipeline.new("new-" + label.replace(" ", "-"), sources))
    other = make_source("c", {"pricing.md": "collides"})
    with pytest.raises(ValidationFailed, match="resolve to"):
        create_pipeline(b, Pipeline.new("collider", [str(other), Source(str(make_source("d", {"pricing.md": "x"})), "d")], target="raw/same/{relpath}"))
    with pytest.raises(ValidationFailed):   # collides with the existing pipeline 'biz'
        create_pipeline(b, Pipeline.new("collider2", [str(other)], target="raw/biz/docs/{relpath}"))
    assert [p.name for p in load_config(b).pipelines] == ["biz"]


# 10. Idempotence ---------------------------------------------------------------------------
def test_s10_second_sync_with_no_changes_makes_zero_writes(world):
    b = world["brain"]
    sync_pipeline(b, "biz")
    snap = tree_snapshot(world["root"] / "raw")
    res = sync_pipeline(b, "biz")
    assert tree_snapshot(world["root"] / "raw") == snap
    assert res.counts()["created"] == res.counts()["updated"] == res.counts()["deleted"] == 0


# Concurrency (review item A4) --------------------------------------------------------------
def test_a4_overlapping_syncs_from_two_processes_never_interleave(world):
    marker = world["root"].parent / "started"
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {SRC!r})
        from pathlib import Path
        from mdpipeline.brain import find_brain
        from mdpipeline.engine import sync_pipeline
        def hook(item):
            Path({str(marker)!r}).write_text("x")
            time.sleep(1.5)
        sync_pipeline(find_brain({str(world['root'])!r}), "biz", after_write=hook)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.time() + 20
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        second = subprocess.run([sys.executable, "-m", "mdpipeline", "--brain", str(world["root"]), "sync", "biz"],
                                capture_output=True, text=True)
        assert second.returncode == 5 and "another sync is running" in (second.stdout + second.stderr)
    finally:
        proc.wait(timeout=60)
    res = sync_pipeline(world["brain"], "biz")
    assert res.counts()["conflicts"] == 0


# 6. Guard ----------------------------------------------------------------------------------
def _ten_files(world):
    for i in range(10):
        write(world["docs"] / f"g{i}.md", f"g{i}")
    sync_pipeline(world["brain"], "biz")


def test_s6_a_sync_above_the_threshold_writes_nothing(world):
    b = world["brain"]
    _ten_files(world)
    for p in list(world["docs"].glob("g*.md")) + [world["docs"] / "plain.md", world["docs"] / "pricing.md"]:
        p.unlink()
    snap = tree_snapshot(world["root"] / "raw")
    from mdpipeline.state import State

    state_before = dict(State.load(b).pipeline("biz").files)
    res = sync_pipeline(b, "biz")
    assert res.result == "blocked" and tree_snapshot(world["root"] / "raw") == snap
    assert State.load(b).pipeline("biz").files == state_before


def test_s6_approval_applies_only_to_the_change_set_it_was_shown(world):
    b = world["brain"]
    _ten_files(world)
    for i in range(7):
        (world["docs"] / f"g{i}.md").unlink()
    sync_pipeline(b, "biz")                              # shown: 7 deletions
    (world["docs"] / "g7.md").unlink()                   # one more file vanishes after it was shown
    snap = tree_snapshot(world["root"] / "raw")
    res = sync_pipeline(b, "biz", approve=True)
    assert res.result == "blocked" and tree_snapshot(world["root"] / "raw") == snap
    res = sync_pipeline(b, "biz", approve=True)           # the new, complete set was shown now
    assert res.result != "blocked" and res.counts()["deleted"] == 8   # (bad.md in specs is a standing, expected error)


def test_s6_the_mcp_cannot_approve_neither_by_engine_nor_by_tool(world):
    b = world["brain"]
    _ten_files(world)
    for i in range(7):
        (world["docs"] / f"g{i}.md").unlink()
    sync_pipeline(b, "biz")
    snap = tree_snapshot(world["root"] / "raw")
    assert sync_pipeline(b, "biz", trigger="mcp", approve=True).result == "blocked"
    import asyncio

    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "mdpipeline", "--brain", str(world["root"]), "mcp"], env=dict(os.environ))
        async with Client(params) as c:
            props = {t.name: set(t.input_schema.get("properties", {})) for t in (await c.list_tools()).tools}
            res = await c.call_tool("sync", {"pipeline": "biz"})
            return props, res

    props, res = asyncio.run(go())
    assert props["sync"] == {"pipeline"}
    assert tree_snapshot(world["root"] / "raw") == snap
