"""MD-17: the write boundary is enforced at execution time by the only writer."""
import os

import pytest

from mdpipeline.brain import init_brain
from mdpipeline.writer import SafeWriter, WriteRefused


@pytest.fixture
def brain(brain_dir):
    return init_brain(brain_dir)


@pytest.fixture
def writer(brain):
    return SafeWriter(brain)


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.name != ".gitignore")


def test_writes_a_markdown_file_inside_raw_creating_folders(writer, brain_dir):
    writer.write("raw/p/sub/a.md", b"hello")
    assert (brain_dir / "raw/p/sub/a.md").read_bytes() == b"hello"


def test_overwrite_replaces_content(writer, brain_dir):
    writer.write("raw/a.md", b"1")
    writer.write("raw/a.md", b"2")
    assert (brain_dir / "raw/a.md").read_bytes() == b"2"


@pytest.mark.parametrize(
    "target",
    ["../outside.md", "/tmp/abs.md", "wiki/x.md", "raw/../wiki/x.md", "raw/a/../../wiki/x.md", ".mdpipeline/x.md",
     "raw", "raw/", "raw//x.md", "raw/./x.md", "raw/x\\y.md", "raw/\x00.md"],
)
def test_hostile_targets_are_refused_and_nothing_is_written(writer, brain_dir, target):
    before = tree(brain_dir)
    with pytest.raises(WriteRefused):
        writer.write(target, b"evil")
    assert tree(brain_dir) == before


@pytest.mark.parametrize("target", ["raw/x.txt", "raw/x.MD", "raw/x", "raw/x.md.exe", "raw/.md"])
def test_only_md_files_are_written(writer, brain_dir, target):
    before = tree(brain_dir)
    with pytest.raises(WriteRefused, match="Markdown"):
        writer.write(target, b"x")
    assert tree(brain_dir) == before


def test_symlinked_target_folder_pointing_at_wiki_is_refused(writer, brain_dir):
    os.symlink(brain_dir / "wiki", brain_dir / "raw" / "link")
    with pytest.raises(WriteRefused, match="symlink"):
        writer.write("raw/link/x.md", b"evil")
    assert not (brain_dir / "wiki" / "x.md").exists()


def test_symlinked_target_folder_pointing_outside_the_brain_is_refused(writer, brain_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, brain_dir / "raw" / "link")
    with pytest.raises(WriteRefused):
        writer.write("raw/link/deep/x.md", b"evil")
    assert list(outside.iterdir()) == []


def test_raw_itself_being_a_symlink_is_refused(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    root = tmp_path / "b"
    root.mkdir()
    os.symlink(real, root / "raw")
    brain = init_brain(root)
    with pytest.raises(WriteRefused):
        SafeWriter(brain).write("raw/x.md", b"evil")
    assert list(real.iterdir()) == []


def test_existing_symlink_at_the_target_is_never_written_through(writer, brain_dir, tmp_path):
    victim = tmp_path / "victim.md"
    victim.write_text("precious")
    os.symlink(victim, brain_dir / "raw" / "a.md")
    with pytest.raises(WriteRefused):
        writer.write("raw/a.md", b"evil")
    assert victim.read_text() == "precious"


def test_target_that_is_a_directory_is_refused(writer, brain_dir):
    (brain_dir / "raw" / "d.md").mkdir()
    with pytest.raises(WriteRefused):
        writer.write("raw/d.md", b"x")


def test_file_name_containing_double_dots_is_just_a_name(writer, brain_dir):
    writer.write("raw/a..b.md", b"ok")
    assert (brain_dir / "raw/a..b.md").exists()


def test_delete_removes_a_file_inside_raw(writer, brain_dir):
    writer.write("raw/a.md", b"1")
    writer.delete("raw/a.md")
    assert not (brain_dir / "raw/a.md").exists()


def test_delete_of_a_missing_file_is_a_noop(writer):
    writer.delete("raw/nothing.md")


@pytest.mark.parametrize("target", ["wiki/keep.md", "../x.md", "raw/../wiki/keep.md", "raw/y.txt"])
def test_delete_outside_raw_or_non_md_is_refused(writer, brain_dir, target):
    (brain_dir / "wiki" / "keep.md").write_text("curated")
    (brain_dir / "raw" / "y.txt").write_text("keep")
    with pytest.raises(WriteRefused):
        writer.delete(target)
    assert (brain_dir / "wiki" / "keep.md").read_text() == "curated"
    assert (brain_dir / "raw" / "y.txt").exists()


def test_delete_through_symlinked_folder_is_refused(writer, brain_dir):
    (brain_dir / "wiki" / "keep.md").write_text("curated")
    os.symlink(brain_dir / "wiki", brain_dir / "raw" / "link")
    with pytest.raises(WriteRefused):
        writer.delete("raw/link/keep.md")
    assert (brain_dir / "wiki" / "keep.md").exists()


def test_delete_never_removes_a_directory(writer, brain_dir):
    (brain_dir / "raw" / "d.md").mkdir()
    with pytest.raises(WriteRefused):
        writer.delete("raw/d.md")


def test_delete_prunes_empty_parent_folders_but_never_raw(writer, brain_dir):
    writer.write("raw/p/sub/a.md", b"1")
    writer.delete("raw/p/sub/a.md")
    assert (brain_dir / "raw").is_dir()
    assert not (brain_dir / "raw" / "p").exists()


def test_delete_keeps_parent_folders_that_still_have_content(writer, brain_dir):
    writer.write("raw/p/a.md", b"1")
    (brain_dir / "raw/p/other.txt").write_text("x")
    writer.delete("raw/p/a.md")
    assert (brain_dir / "raw/p/other.txt").exists()


def test_failed_replace_leaves_the_old_file_intact_and_no_temp_files(writer, brain_dir, monkeypatch):
    writer.write("raw/a.md", b"old")

    def boom(*args, **kwargs):
        raise OSError("disk exploded")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        writer.write("raw/a.md", b"new")
    monkeypatch.undo()
    assert (brain_dir / "raw/a.md").read_bytes() == b"old"
    assert [p for p in (brain_dir / ".mdpipeline").rglob("*") if p.suffix == ".tmp"] == []
    assert tree(brain_dir / "raw") == ["a.md"]
