"""MD-13: one-way, incremental, crash-safe copy; plus MD-14 provenance seen end to end."""
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest
import yaml

from conftest import tree_snapshot, write
from mdpipeline.config import Pipeline, Source
from mdpipeline.engine import sync_pipeline
from mdpipeline.frontmatter import parse_document
from mdpipeline.state import State
from mdpipeline.writer import SafeWriter


class Kill(BaseException):
    """Simulates the process being killed (not caught by `except Exception`)."""


def read_brain(brain, rel):
    return (brain.root / rel).read_text()


def test_first_sync_copies_markdown_with_provenance(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "# A\n", "sub/b.md": "---\ntitle: B\n---\nbody\n", "skip.txt": "no"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["created"] == 2
    meta, body = parse_document(read_brain(brain, "raw/p/docs/sub/b.md"))
    assert meta["title"] == "B" and meta["provenance"]["pipeline"] == "p" and body == "body\n"
    assert not (brain.root / "raw/p/docs/skip.txt").exists()


def test_source_bytes_and_mtimes_are_unchanged_after_any_sync(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A", "b/c.md": "C"})
    os.utime(src / "a.md", (1_600_000_000, 1_600_000_000))
    add_pipeline(Pipeline.new("p", [str(src)]))
    before = tree_snapshot(src)
    sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    sync_pipeline(brain, "p", trigger="cli")
    (src / "a.md").stat()  # source reads must not alter content
    (brain.root / "raw/p/docs/a.md").write_text("edited in brain")
    sync_pipeline(brain, "p", trigger="cli", force=True)
    assert tree_snapshot(src) == before


def test_second_sync_with_no_changes_performs_zero_writes(brain, add_pipeline, make_source, monkeypatch):
    src = make_source("docs", {"a.md": "A", "b.md": "B"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    raw_before = tree_snapshot(brain.raw_dir)
    writes = []
    orig = SafeWriter.write
    monkeypatch.setattr(SafeWriter, "write", lambda self, rel, data: (writes.append(rel), orig(self, rel, data)))
    deletes = []
    monkeypatch.setattr(SafeWriter, "delete", lambda self, rel: deletes.append(rel))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert writes == [] and deletes == []
    assert res.counts()["unchanged"] == 2 and res.result == "ok"
    assert tree_snapshot(brain.raw_dir) == raw_before


def test_changes_are_detected_by_content_not_only_timestamp(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "AAAA"})
    os.utime(src / "a.md", (1_600_000_000, 1_600_000_000))
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    (src / "a.md").write_text("BBBB")  # same size
    os.utime(src / "a.md", (1_600_000_000, 1_600_000_000))  # same mtime
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["updated"] == 1
    assert "BBBB" in read_brain(brain, "raw/p/docs/a.md")


def test_touching_a_source_without_changing_content_writes_nothing(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    raw_before = tree_snapshot(brain.raw_dir)
    os.utime(src / "a.md", (1_700_000_000, 1_700_000_000))
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["unchanged"] == 1 and tree_snapshot(brain.raw_dir) == raw_before


def test_dry_run_counts_match_the_real_run_that_follows(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A", "b.md": "B", ".x/c.md": "C"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    dry = sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    real = sync_pipeline(brain, "p", trigger="cli")
    assert dry.counts() == real.counts()
    assert dry.dry_run is True and real.dry_run is False


def test_dry_run_writes_nothing_not_even_state_or_log(brain, brain_dir, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    before = tree_snapshot(brain_dir)
    sync_pipeline(brain, "p", trigger="cli", dry_run=True)
    assert tree_snapshot(brain_dir) == before


def test_symlinks_are_never_followed_out_of_the_source(brain, add_pipeline, make_source, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("SECRET")
    src = make_source("docs", {"ok.md": "ok"})
    os.symlink(outside / "secret.md", src / "link.md")
    os.symlink(outside, src / "dirlink")
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    copied = {str(p.relative_to(brain.raw_dir)) for p in brain.raw_dir.rglob("*") if p.is_file()}
    assert copied == {"p/docs/ok.md"}
    assert "SECRET" not in "".join(p.read_text() for p in brain.raw_dir.rglob("*.md"))


def test_interrupted_sync_leaves_no_partial_file_and_rerun_converges(brain, add_pipeline, make_source):
    files = {f"n{i}.md": f"# note {i}\n" * 50 for i in range(8)}
    src = make_source("docs", files)
    add_pipeline(Pipeline.new("p", [str(src)]))
    count = {"n": 0}

    def die_after_three(item):
        count["n"] += 1
        if count["n"] == 3:
            raise Kill()

    with pytest.raises(Kill):
        sync_pipeline(brain, "p", trigger="cli", after_write=die_after_three)
    written = sorted((brain.raw_dir / "p/docs").glob("*.md"))
    assert len(written) == 3
    for f in written:  # each existing file is complete and valid
        assert parse_document(f.read_text())[0]["provenance"]["pipeline"] == "p"
    assert [p for p in brain.config_dir.rglob("*.tmp")] == []
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["conflicts"] == 0
    assert len(list((brain.raw_dir / "p/docs").glob("*.md"))) == 8
    again = sync_pipeline(brain, "p", trigger="cli")
    assert again.counts()["unchanged"] == 8


def test_interrupted_update_of_an_existing_file_converges_without_false_conflict(brain, add_pipeline, make_source):
    src = make_source("docs", {f"n{i}.md": "v1" for i in range(4)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    for i in range(4):
        (src / f"n{i}.md").write_text("v2")
    n = {"i": 0}

    def die(item):
        n["i"] += 1
        if n["i"] == 2:
            raise Kill()

    with pytest.raises(Kill):
        sync_pipeline(brain, "p", trigger="cli", after_write=die)
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.counts()["conflicts"] == 0 and res.result == "ok"
    for i in range(4):
        assert "v2" in read_brain(brain, f"raw/p/docs/n{i}.md")


def test_sigkill_of_a_real_process_mid_sync_converges(brain, add_pipeline, make_source, tmp_path):
    src = make_source("docs", {f"n{i}.md": f"# {i}\n" * 200 for i in range(20)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(os.path.dirname(__file__) + '/../src')!r})
        from pathlib import Path
        from mdpipeline.brain import find_brain
        from mdpipeline.engine import sync_pipeline
        n = [0]
        def hook(item):
            n[0] += 1
            if n[0] == 5:
                os.kill(os.getpid(), 9)
        sync_pipeline(find_brain({str(brain.root)!r}), "p", trigger="cli", after_write=hook)
    """)
    proc = subprocess.run([sys.executable, "-c", script])
    assert proc.returncode == -signal.SIGKILL
    for f in (brain.raw_dir / "p/docs").glob("*.md"):
        assert parse_document(f.read_text())[0]["provenance"]["content_hash"].startswith("sha256:")
    res = sync_pipeline(brain, "p", trigger="cli")
    assert res.result == "ok" and res.counts()["conflicts"] == 0
    assert len(list((brain.raw_dir / "p/docs").glob("*.md"))) == 20
    assert sync_pipeline(brain, "p", trigger="cli").counts()["unchanged"] == 20


def test_reference_fixture_500_files_of_5kb_syncs_in_under_10_seconds(brain, add_pipeline, make_source):
    body = ("lorem ipsum dolor sit amet " * 200)[:5000]
    src = make_source("big", {f"d{i % 10}/f{i}.md": f"# {i}\n{body}" for i in range(500)})
    add_pipeline(Pipeline.new("p", [str(src)]))
    start = time.monotonic()
    res = sync_pipeline(brain, "p", trigger="cli")
    elapsed = time.monotonic() - start
    assert res.counts()["created"] == 500 and elapsed < 10
    start = time.monotonic()
    assert sync_pipeline(brain, "p", trigger="cli").counts()["unchanged"] == 500
    assert time.monotonic() - start < 10


def test_state_records_ownership_of_every_written_file(brain, add_pipeline, make_source):
    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="cli")
    rec = State.load(brain).pipeline("p").files["raw/p/docs/a.md"]
    assert rec.source_label == "docs" and rec.source_path == "a.md" and rec.pending_hash is None


def test_a_run_is_logged_with_the_trigger(brain, add_pipeline, make_source):
    from mdpipeline.log import ActivityLog

    src = make_source("docs", {"a.md": "A"})
    add_pipeline(Pipeline.new("p", [str(src)]))
    sync_pipeline(brain, "p", trigger="mcp")
    run = ActivityLog(brain).read(types={"run"})[-1]
    assert run["trigger"] == "mcp" and run["pipeline"] == "p" and run["result"] == "ok"
