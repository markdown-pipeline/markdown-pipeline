"""Product order 2026-10-29: the public tree must equal the released source, every release. The sync script copies tracked files, keeps the withheld
workflows out, makes the three root copies, deletes what is gone, and never commits."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sync_public", APP / "scripts" / "sync_public.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def trees(tmp_path):
    private, public = tmp_path / "private", tmp_path / "public"
    for root in (private, public):
        root.mkdir()
        git(root, "init", "-q")
    for rel, text in {"src/a.py": "A", "scripts/install.sh": "#!/bin/sh", "docs/what-we-send.md": "W", "docs/usage-events.md": "U",
                      ".github/workflows/windows-tests.yml": "x", ".github/workflows/publish-registry.yml": "y", ".github/workflows/linux-tests.yml": "z",
                      "glama.json": "{}"}.items():
        (private / rel).parent.mkdir(parents=True, exist_ok=True)
        (private / rel).write_text(text)
    git(private, "add", "-A")
    (public / "old.txt").write_text("gone")
    git(public, "add", "-A")
    return private, public


def test_the_public_tree_gets_every_tracked_file_except_the_withheld_workflows(trees):
    private, public = trees
    sync.apply(public, private)
    assert (public / "src/a.py").read_text() == "A" and (public / "glama.json").exists() and (public / ".github/workflows/linux-tests.yml").exists()
    assert not (public / ".github/workflows/windows-tests.yml").exists() and not (public / ".github/workflows/publish-registry.yml").exists()


def test_the_three_root_copies_are_made(trees):
    private, public = trees
    sync.apply(public, private)
    assert [(public / n).read_text() for n in ("install.sh", "usage-events.md", "what-we-send.md")] == ["#!/bin/sh", "U", "W"]


def test_a_file_that_no_longer_exists_is_deleted(trees):
    private, public = trees
    sync.apply(public, private)
    assert not (public / "old.txt").exists()


def test_the_plan_changes_nothing_and_a_second_apply_has_nothing_left_to_do(trees):
    private, public = trees
    write, delete = sync.plan(public, private)
    assert write and delete == ["old.txt"] and (public / "old.txt").exists() and not (public / "src/a.py").exists()
    sync.apply(public, private)
    assert sync.plan(public, private) == ([], [])


def test_the_script_never_commits(trees):
    private, public = trees
    sync.apply(public, private)
    assert subprocess.run(["git", "log"], cwd=public, capture_output=True).returncode != 0          # still no commit in the clone
