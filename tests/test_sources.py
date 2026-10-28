"""Local-folder source: enumeration with include/exclude, symlink safety, missing folders."""
import os

from mdpipeline.config import DEFAULT_EXCLUDE, DEFAULT_INCLUDE, Source
from mdpipeline.sources import LocalFolderSource
import pytest
import winsupport


def scan(root, include=None, exclude=None, label="s"):
    return LocalFolderSource(Source(str(root), label)).scan(include or DEFAULT_INCLUDE, exclude if exclude is not None else DEFAULT_EXCLUDE)


def by_rel(result):
    return {e.relpath: e for e in result.entries}


def test_lists_markdown_files_posix_sorted(make_source):
    root = make_source("a", {"b.md": "1", "sub/c.md": "2", "a.md": "3", "x.txt": "no"})
    res = scan(root)
    assert res.status == "ok"
    assert [e.relpath for e in res.entries] == ["a.md", "b.md", "sub/c.md"]
    assert all(e.status == "candidate" for e in res.entries)


def test_entry_carries_size_mtime_and_reads_bytes(make_source):
    root = make_source("a", {"a.md": "héllo"})
    e = scan(root).entries[0]
    assert e.read_bytes() == "héllo".encode()
    assert e.size == len("héllo".encode())
    assert e.mtime == os.stat(root / "a.md").st_mtime


def test_excluded_md_files_are_reported_with_the_matching_rule(make_source):
    root = make_source("a", {"keep.md": "1", ".claude/skills/x.md": "2", "docs/.ag/y.md": "3"})
    res = by_rel(scan(root))
    assert res["keep.md"].status == "candidate"
    assert res[".claude/skills/x.md"].status == "skip"
    assert res[".claude/skills/x.md"].reason == "excluded by rule **/.*/**"
    assert res["docs/.ag/y.md"].status == "skip"


def test_include_match_that_is_not_md_is_skipped_with_reason_not_renamed(make_source):
    root = make_source("a", {"n.markdown": "1", "ok.md": "2"})
    res = by_rel(scan(root, include=["**/*.markdown", "**/*.md"]))
    assert res["n.markdown"].status == "skip"
    assert res["n.markdown"].reason == "not a .md file"
    assert res["ok.md"].status == "candidate"


def test_files_not_matching_include_are_ignored(make_source):
    root = make_source("a", {"a.md": "1", "b.txt": "2"})
    assert [e.relpath for e in scan(root).entries] == ["a.md"]


def test_git_and_node_modules_are_pruned_not_listed(make_source):
    root = make_source("a", {"a.md": "1", "node_modules/p/readme.md": "2", ".git/x.md": "3"})
    assert [e.relpath for e in scan(root).entries] == ["a.md"]


def test_symlinks_are_never_followed(make_source, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    root = make_source("a", {"real.md": "1"})
    os.symlink(outside / "secret.md", root / "link.md")
    os.symlink(outside, root / "linkdir")
    res = by_rel(scan(root))
    assert set(res) == {"real.md", "link.md"}
    assert res["link.md"].status == "skip" and "symlink" in res["link.md"].reason


def test_missing_source_reports_status_missing_with_no_entries(tmp_path):
    res = scan(tmp_path / "gone")
    assert res.status == "missing" and res.entries == []


def test_source_that_is_a_file_is_unreadable(tmp_path):
    f = tmp_path / "file.md"
    f.write_text("x")
    assert scan(f).status == "unreadable"


@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_unreadable_file_becomes_an_error_entry(make_source):
    root = make_source("a", {"a.md": "1", "b.md": "2"})
    os.chmod(root / "b.md", 0)
    try:
        res = by_rel(scan(root))
        # stat works, reading fails when the bytes are requested
        try:
            res["b.md"].read_bytes()
            readable = True
        except OSError:
            readable = False
        assert readable is False or winsupport.is_root()
    finally:
        os.chmod(root / "b.md", 0o644)


@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_unreadable_source_directory_is_status_unreadable(make_source):
    root = make_source("a", {"a.md": "1"})
    os.chmod(root, 0)
    try:
        res = scan(root)
        assert res.status == "unreadable" or winsupport.is_root()
    finally:
        os.chmod(root, 0o755)


@pytest.mark.windows_unsupported("chmod makes a file or folder unreadable only on Unix; Windows ignores the permission bits")
def test_unreadable_subdirectory_makes_the_whole_source_unreadable_so_nothing_looks_deleted(make_source):
    root = make_source("a", {"a.md": "1", "sub/b.md": "2"})
    os.chmod(root / "sub", 0)
    try:
        res = scan(root)
        assert res.status == "unreadable" or winsupport.is_root()
    finally:
        os.chmod(root / "sub", 0o755)
